# Copyright 2014 The Swarming Authors. All rights reserved.
# Use of this source code is governed by the Apache v2.0 license that can be
# found in the LICENSE file.

"""Task entity that describe when a task is to be scheduled.

This module doesn't do the scheduling itself. It only describes the tasks ready
to be scheduled.

Graph of the schema:
     <See task_request.py>
               ^
               |
    +---------------------+
    |TaskRequest          |  (task_request.py)
    |    +--------------+ |
    |    |TaskProperties| |
    |    +--------------+ |
    +---------------------+
               ^
               |
          +---------+
          |TaskToRun|
          +---------+
"""

import datetime
import hashlib
import itertools
import logging
import struct

from google.appengine.api import datastore_errors
from google.appengine.api import memcache
from google.appengine.ext import ndb
from google.appengine.runtime import apiproxy_errors

from components import utils
from server import task_common
from server import task_request


class TaskToRun(ndb.Model):
  """Defines a TaskRequest ready to be scheduled on a bot.

  This specific request for a specific task can be executed multiple times,
  each execution will create a new child task_result.TaskResult of
  task_result.TaskResultSummary.

  This entity must be kept small and contain the minimum data to enable the
  queries for two reasons:
  - it is updated inside a transaction for each scheduling event, e.g. when a
    bot gets assigned this task item to work on.
  - all the ones currently active are fetched at once in a cron job.

  The key id 1 one, parent is TaskRequest.
  """
  # The shortened hash of TaskRequests.dimensions_json. This value is generated
  # with _hash_dimensions().
  dimensions_hash = ndb.IntegerProperty(indexed=False, required=True)

  # Moment by which the task has to be requested by a bot. Copy of TaskRequest's
  # TaskRequest.expiration_ts to enable queries when cleaning up stale jobs.
  expiration_ts = ndb.DateTimeProperty(required=True)

  # Everything above is immutable, everything below is mutable.

  # priority and request creation timestamp are mixed together to allow queries
  # to order the results by this field to allow sorting by priority first, and
  # then timestamp. See _gen_queue_number_key() for details. This value is only
  # set when the task is available to run, i.e.
  # ndb.TaskResult.query(ancestor=self.key).get().state==AVAILABLE.
  # If this task it not ready to be scheduled, it must be None.
  queue_number = ndb.IntegerProperty()

  @property
  def request_key(self):
    """Returns the TaskRequest ndb.Key that is parent to the task to run."""
    return task_to_run_key_to_request_key(self.key)


def _gen_queue_number_key(timestamp, priority):
  """Generates a 64 bit packed value used for TaskToRun.queue_number.

  The lower value the higher importance.

  Arguments:
  - timestamp: datetime.datetime when the TaskRequest was filed in.
  - priority: priority of the TaskRequest.

  Returns:
    queue_number is a 64 bit integer:
    - 1 bit highest order bit set to 0 to detect overflow.
    - 8 bits of priority.
    - 47 bits at 1ms resolution is 2**47 / 365 / 24 / 60 / 60 / 1000 = 4462
      years.
    - The last 8 bits is currently unused and set to 0.
  """
  assert isinstance(timestamp, datetime.datetime)
  task_common.validate_priority(priority)
  now = task_common.milliseconds_since_epoch(timestamp)
  assert 0 <= now < 2**47, hex(now)
  # Assumes the system runs a 64 bits version of python.
  return priority << 55 | now << 8


def _explode_list(values):
  """Yields all the permutations in the dict values for items which are list.

  Examples:
  - values={'a': [1, 2]} yields {'a': 1}, {'a': 2}
  - values={'a': [1, 2], 'b': [3, 4]} yields {'a': 1, 'b': 3}, {'a': 2, 'b': 3},
    {'a': 1, 'b': 4}, {'a': 2, 'b': 4}
  """
  key_has_list = frozenset(
      k for k, v in values.iteritems() if isinstance(v, list))
  if not key_has_list:
    yield values
    return

  all_keys = frozenset(values)
  key_others = all_keys - key_has_list
  non_list_dict = {k: values[k] for k in key_others}
  options = [[(k, v) for v in values[k]] for k in sorted(key_has_list)]
  for i in itertools.product(*options):
    out = non_list_dict.copy()
    out.update(i)
    yield out


def _powerset(dimensions):
  """Yields the product of all the possible permutations in dimensions.

  Starts with the most restrictive set and goes down to the less restrictive
  ones. See unit test TaskToRunPrivateTest.test_powerset for examples.
  """
  keys = sorted(dimensions)
  for i in itertools.chain.from_iterable(
      itertools.combinations(keys, r) for r in xrange(len(keys), -1, -1)):
    for i in _explode_list({k: dimensions[k] for k in i}):
      yield i


def _put_task_to_run(task_key, queue_number):
  """Updates a TaskToRun.queue_number value.

  This function enforces that TaskToRun.queue_number member is toggled and runs
  inside a transaction.

  Arguments:
  - task_key: ndb.Key to TaskToRun entity to update.
  - queue_number: new queue_number value. Can be either None to mark that the
    task is not available anymore or an int so this task becomes (r)enabled for
    reaping.

  Returns:
    True if succeeded, False if no modification was committed to the DB.
  """
  assert task_key.kind() == 'TaskToRun', task_key
  assert ndb.in_transaction()
  # Refresh the item.
  task = task_key.get()
  if bool(queue_number) == bool(task.queue_number):
    # Too bad, someone else reaped it in the meantime.
    return False
  task.queue_number = queue_number
  task.put()
  return True


def _hash_dimensions(dimensions_json):
  """Returns a 32 bits int that is a hash of the dimensions specified.

  dimensions_json must already be encoded as json. This is because
  TaskProperties already has the json encoded data available.
  """
  digest = hashlib.md5(dimensions_json).digest()
  # Note that 'L' means C++ unsigned long which is (usually) 32 bits and
  # python's int is 64 bits.
  return int(struct.unpack('<L', digest[:4])[0])


def _memcache_to_run_key(task_key):
  """Functional equivalent of task_common.pack_result_summary_key()."""
  request_key = task_to_run_key_to_request_key(task_key)
  return '%x' % request_key.integer_id()


def _set_lookup_cache(task_key, is_available_to_schedule):
  """Updates the quick lookup cache to mark an item as available or not.

  This cache is a blacklist of items that are already reaped, so it is not worth
  trying to reap it with a DB transaction. This saves on DB contention when a
  high number (>1000) of concurrent bots with similar dimension are reaping
  tasks simultaneously. In this case, there is a high likelihood that multiple
  concurrent HTTP handlers are trying to reap the exact same task
  simultaneously. This blacklist helps reduce the contention.
  """
  # Set the expiration time for items in the negative cache as 2 minutes. This
  # copes with significant index inconsistency but do not clog the memcache
  # server with unneeded keys.
  cache_lifetime = 120

  key = _memcache_to_run_key(task_key)
  if is_available_to_schedule:
    # The item is now available, so remove it from memcache.
    memcache.delete(key, namespace='task_to_run')
  else:
    memcache.set(key, True, time=cache_lifetime, namespace='task_to_run')


def _lookup_cache_is_taken(task_key):
  """Queries the quick lookup cache to reduce DB operations."""
  key = _memcache_to_run_key(task_key)
  return bool(memcache.get(key, namespace='task_to_run'))


### Public API.


def request_key_to_task_to_run_key(request_key):
  """Returns the ndb.Key for a TaskToRun from a TaskRequest key."""
  assert request_key.kind() == 'TaskRequest', request_key
  return ndb.Key(TaskToRun, 1, parent=request_key)


def task_to_run_key_to_request_key(task_key):
  """Returns the ndb.Key for a TaskToRun from a TaskRequest key."""
  if task_key.kind() != 'TaskToRun':
    raise ValueError('Expected key to TaskToRun, got %s' % task_key.kind())
  return task_key.parent()


def new_task_to_run(request):
  """Returns a fresh new TaskToRun for the task ready to be scheduled.

  Returns:
    Unsaved TaskToRun entity.
  """
  dimensions_json = utils.encode_to_json(request.properties.dimensions)
  return TaskToRun(
      key=request_key_to_task_to_run_key(request.key),
      queue_number=_gen_queue_number_key(request.created_ts, request.priority),
      dimensions_hash=_hash_dimensions(dimensions_json),
      expiration_ts=request.expiration_ts)


def validate_to_run_key(task_key):
  """Validates a ndb.Key to a TaskToRun entity. Raises ValueError if invalid."""
  # This also validates the key kind.
  request_key = task_to_run_key_to_request_key(task_key)
  if task_key.integer_id() != 1:
    raise ValueError('TaskToRun key id should be 1, found %s' % task_key.id())
  task_request.validate_request_key(request_key)


def yield_next_available_task_to_dispatch(bot_dimensions):
  """Yields next available (TaskRequest, TaskToRun) in decreasing order of
  priority.

  Once the caller determines the task is suitable to execute, it must use
  reap_task_to_run(task.key) to mark that it is not to be scheduled anymore.

  Performance is the top most priority here.

  Arguments:
  - bot_dimensions: dimensions (as a dict) defined by the bot that can be
      matched.
  """
  # List of all the valid dimensions hashed.
  accepted_dimensions_hash = frozenset(
      _hash_dimensions(utils.encode_to_json(i))
      for i in _powerset(bot_dimensions))
  now = task_common.utcnow()
  broken = 0
  cache_lookup = 0
  expired = 0
  hash_mismatch = 0
  ignored = 0
  no_queue = 0
  real_mismatch = 0
  total = 0
  # Be very aggressive in fetching the largest amount of items as possible. Note
  # that we use the default ndb.EVENTUAL_CONSISTENCY so stale items may be
  # returned. It's handled specifically.
  # - 100/200 gives 2s~40s of query time for 1275 items.
  # - 250/500 gives 2s~50s of query time for 1275 items.
  # - 50/500 gives 3s~20s of query time for 1275 items. (Slower but less
  #   variance). Spikes in 20s~40s are rarer.
  # The problem here are:
  # - Outliers, some shards are simply slower at executing the query.
  # - Median time, which we should optimize.
  # - Abusing batching will slow down this query.
  #
  # TODO(maruel): Measure query performance with stats_framework!!
  # TODO(maruel): Use a keys_only=True query + fetch_page_async() +
  # ndb.get_multi_async() + memcache.get_multi_async() to do pipelined
  # processing. Should greatly reduce the effect of latency on the total
  # duration of this function. I also suspect using ndb.get_multi() will return
  # fresher objects than what is returned by the query.
  opts = ndb.QueryOptions(batch_size=50, prefetch_size=500, use_cache=False)
  try:
    # Interestingly, the filter on .queue_number>0 is required otherwise all the
    # None items are returned first.
    q = TaskToRun.query(default_options=opts).order(
        TaskToRun.queue_number).filter(TaskToRun.queue_number > 0)
    for task in q:
      duration = (task_common.utcnow() - now).total_seconds()
      if duration > 40.:
        # Stop searching after too long, since the odds of the request blowing
        # up right after succeeding in reaping a task is not worth the dangling
        # task request that will stay in limbo until the cron job reaps it and
        # retry it. The current handlers are given 60s to complete. By using
        # 40s, it gives 20s to complete the reaping and complete the HTTP
        # request.
        return

      total += 1
      # Verify TaskToRun is what is expected. Play defensive here.
      try:
        validate_to_run_key(task.key)
      except ValueError as e:
        logging.error(str(e))
        broken += 1
        continue

      # It is possible for the index to be inconsistent since it is not executed
      # in a transaction, no problem.
      if not task.queue_number:
        no_queue += 1
        continue
      # It expired. A cron job will cancel it eventually. Since 'now' is saved
      # before the query, an expired task may still be reaped even if
      # technically expired if the query is very slow. This is on purpose so
      # slow queries do not cause exagerate expirations.
      if task.expiration_ts < now:
        expired += 1
        continue
      if task.dimensions_hash not in accepted_dimensions_hash:
        hash_mismatch += 1
        continue

      # Do this after the basic weeding out but before fetching TaskRequest.
      if _lookup_cache_is_taken(task.key):
        cache_lookup += 1
        continue

      # The hash may have conflicts. Ensure the dimensions actually match by
      # verifying the TaskRequest. There's a probability of 2**-31 of conflicts,
      # which is low enough for our purpose. The reason use_cache=False is
      # otherwise it'll create a buffer bloat.
      request = task.request_key.get(use_cache=False)
      if not task_common.match_dimensions(
          request.properties.dimensions, bot_dimensions):
        real_mismatch += 1
        continue

      # It's a valid task! Note that in the meantime, another bot may have
      # reaped it.
      yield request, task
      ignored += 1
  finally:
    duration = (task_common.utcnow() - now).total_seconds()
    logging.info(
        '%d/%s in %5.2fs: %d total, %d exp %d no_queue, %d hash mismatch, '
        '%d cache negative, %d dimensions mismatch, %d ignored, %d broken',
        opts.batch_size,
        opts.prefetch_size,
        duration,
        total,
        expired,
        no_queue,
        cache_lookup,
        hash_mismatch,
        real_mismatch,
        ignored,
        broken)


def yield_expired_task_to_run():
  """Yields all the expired TaskToRun still marked as available."""
  now = task_common.utcnow()
  for task in TaskToRun.query().filter(TaskToRun.queue_number > 0):
    if task.expiration_ts < now:
      yield task


def retry_task_to_run(task_key):
  """Updates a TaskToRun to note it should run again.

  The modification is done in a transaction to ensure .queue_number is toggled.

  Uses the original timestamp for priority, so the task request doesn't get
  starved on retries.

  Arguments:
  - task_key: ndb.Key to TaskToRun entity to update.

  Returns:
    True if succeeded.
  """
  assert task_key.kind() == 'TaskToRun', task_key
  assert not ndb.in_transaction()
  request = task_to_run_key_to_request_key(task_key).get()
  queue_number = _gen_queue_number_key(request.created_ts, request.priority)
  try:
    result = ndb.transaction(
        lambda: _put_task_to_run(task_key, queue_number), retries=1)
    if result:
      _set_lookup_cache(task_key, True)
    return result
  except (
      apiproxy_errors.CancelledError,
      datastore_errors.BadRequestError,
      datastore_errors.Timeout,
      datastore_errors.TransactionFailedError,
      RuntimeError):
    return False


def reap_task_to_run(task_key):
  """Updates a TaskToRun to note it should not be run again.

  It must run inside a transaction to ensure .queue_number is toggled. The
  transaction should have retries=0 but this is not enforced here.

  Arguments:
  - task_key: ndb.Key to TaskToRun entity to update.
  - request: TaskRequest with necessary details.

  Returns:
    True if succeeded.
  """
  assert task_key.kind() == 'TaskToRun', task_key
  assert ndb.in_transaction()
  result = _put_task_to_run(task_key, None)
  if result:
    # Note this fact immediately in memcache to reduce DB contention.
    _set_lookup_cache(task_key, False)
  return result


def abort_task_to_run(task):
  """Updates a TaskToRun to note it should not be scheduled for work.

  Arguments:
  - task: TaskToRun entity to update.
  """
  # TODO(maruel): Add support to kill an on-going task and update the
  # corresponding TaskRunResult.
  # TODO(maruel): Add stats.
  assert not ndb.in_transaction()
  assert isinstance(task, TaskToRun), task
  task.queue_number = None
  task.put()
  # Add it to the negative cache.
  _set_lookup_cache(task.key, False)
