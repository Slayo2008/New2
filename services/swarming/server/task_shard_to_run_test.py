#!/usr/bin/env python
# Copyright 2014 The Swarming Authors. All rights reserved.
# Use of this source code is governed by the Apache v2.0 license that can be
# found in the LICENSE file.

import datetime
import hashlib
import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import test_env

test_env.setup_test_env()

from google.appengine.ext import ndb

from components import utils
from server import task_request
from server import task_shard_to_run as task_to_run
from server import test_helper
from support import test_case

# pylint: disable=W0212


def _gen_request_data(properties=None, **kwargs):
  base_data = {
    'name': 'Request name',
    'user': 'Jesus',
    'properties': {
      'commands': [[u'command1']],
      'data': [],
      'dimensions': {},
      'env': {},
      'execution_timeout_secs': 24*60*60,
      'io_timeout_secs': None,
    },
    'priority': 50,
    'scheduling_expiration_secs': 60,
  }
  base_data.update(kwargs)
  base_data['properties'].update(properties or {})
  return base_data


def _task_to_run_to_dict(i):
  """Converts the queue_number to hex for easier testing."""
  out = i.to_dict()
  # Consistent formatting makes it easier to reason about.
  out['queue_number'] = '0x%016x' % out['queue_number']
  return out


def _yield_next_available_task_to_dispatch(bot_dimensions):
  return [
    _task_to_run_to_dict(task)
    for _request, task in
        task_to_run.yield_next_available_task_to_dispatch(bot_dimensions)
  ]


def _gen_new_task_to_run(**kwargs):
  """Returns a TaskToRun saved in the DB."""
  data = _gen_request_data(**kwargs)
  task = task_to_run.new_task_to_run(task_request.make_request(data))
  task.put()
  return task


def _hash_dimensions(dimensions):
  return task_to_run._hash_dimensions(utils.encode_to_json(dimensions))


class TaskToRunPrivateTest(test_case.TestCase):
  def test_gen_queue_number_key(self):
    # tuples of (input, expected).
    # 2**47 / 365 / 24 / 60 / 60 / 1000. = 4462.756
    data = [
      (('1970-01-01 00:00:00.000',   0), '0x0000000000000000'),
      (('1970-01-01 00:00:00.000', 255), '0x7f80000000000000'),
      (('1970-01-01 00:00:00.040',   0), '0x0000000000002800'),
      (('1970-01-01 00:00:00.050',   0), '0x0000000000003200'),
      (('1970-01-01 00:00:00.100',   0), '0x0000000000006400'),
      (('1970-01-01 00:00:00.900',   0), '0x0000000000038400'),
      (('1970-01-01 00:00:01.000',   0), '0x000000000003e800'),
      (('1970-01-01 00:00:00.000',   1), '0x0080000000000000'),
      (('1970-01-01 00:00:00.000',   2), '0x0100000000000000'),
      (('2010-01-02 03:04:05.060',   0), '0x000125ecfd5cc400'),
      (('2010-01-02 03:04:05.060',   1), '0x008125ecfd5cc400'),
      # It's the end of the world as we know it...
      (('6429-10-17 02:45:55.327',   0), '0x007fffffffffff00'),
      (('6429-10-17 02:45:55.327', 255), '0x7fffffffffffff00'),
      (('6429-10-17 02:45:55.327', 255), '0x7fffffffffffff00'),
    ]
    for (timestamp, priority), expected in data:
      d = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
      actual = '0x%016x' % task_to_run._gen_queue_number_key(d, priority)
      self.assertEquals(expected, actual)

  def test_powerset(self):
    # tuples of (input, expected).
    # TODO(maruel): We'd want the code to deterministically try 'Windows-6.1'
    # before 'Windows'. Probably do a reverse() on the values?
    data = [
      ({'OS': 'Windows'}, [{'OS': 'Windows'}, {}]),
      (
        {'OS': ['Windows', 'Windows-6.1']},
        [{'OS': 'Windows'}, {'OS': 'Windows-6.1'}, {}],
      ),
      (
        {'OS': ['Windows', 'Windows-6.1'], 'hostname': 'foo'},
        [
          {'OS': 'Windows', 'hostname': 'foo'},
          {'OS': 'Windows-6.1', 'hostname': 'foo'},
          {'OS': 'Windows'},
          {'OS': 'Windows-6.1'},
          {'hostname': 'foo'},
          {},
        ],
      ),
      (
        {'OS': ['Windows', 'Windows-6.1'], 'hostname': 'foo', 'bar': [2, 3]},
        [
         {'OS': 'Windows', 'bar': 2, 'hostname': 'foo'},
          {'OS': 'Windows', 'bar': 3, 'hostname': 'foo'},
          {'OS': 'Windows-6.1', 'bar': 2, 'hostname': 'foo'},
          {'OS': 'Windows-6.1', 'bar': 3, 'hostname': 'foo'},
          {'OS': 'Windows', 'bar': 2},
          {'OS': 'Windows', 'bar': 3},
          {'OS': 'Windows-6.1', 'bar': 2},
          {'OS': 'Windows-6.1', 'bar': 3},
          {'OS': 'Windows', 'hostname': 'foo'},
          {'OS': 'Windows-6.1', 'hostname': 'foo'},
          {'bar': 2, 'hostname': 'foo'},
          {'bar': 3, 'hostname': 'foo'},
          {'OS': 'Windows'},
          {'OS': 'Windows-6.1'},
          {'bar': 2},
          {'bar': 3},
          {'hostname': 'foo'},
          {},
        ],
      ),
    ]
    for inputs, expected in data:
      actual = list(task_to_run._powerset(inputs))
      self.assertEquals(expected, actual)

  def test_hash_dimensions(self):
    dimensions = 'this is not json'
    as_hex = hashlib.md5(dimensions).digest()[:4].encode('hex')
    actual = task_to_run._hash_dimensions(dimensions)
    # It is exactly the same bytes reversed (little endian). It's positive even
    # with bit 31 set because python stores it internally as a int64.
    self.assertEqual('711d0bf1', as_hex)
    self.assertEqual(0xf10b1d71, actual)


class TaskToRunApiTest(test_case.TestCase):
  def setUp(self):
    super(TaskToRunApiTest, self).setUp()
    self.now = datetime.datetime(2014, 01, 02, 03, 04, 05, 06)
    test_helper.mock_now(self, self.now)
    # The default scheduling_expiration_secs for _gen_request_data().
    self.expiration_ts = self.now + datetime.timedelta(seconds=60)

  def test_all_apis_are_tested(self):
    actual = frozenset(i[5:] for i in dir(self) if i.startswith('test_'))
    # Contains the list of all public APIs.
    expected = frozenset(
        i for i in dir(task_to_run)
        if i[0] != '_' and hasattr(getattr(task_to_run, i), 'func_name'))
    missing = expected - actual
    self.assertFalse(missing)

  def test_task_to_run_key_to_request_key(self):
    request_key = task_request.id_to_request_key(256)
    task_key = task_to_run.request_key_to_task_to_run_key(request_key)
    actual = task_to_run.task_to_run_key_to_request_key(task_key)
    self.assertEqual(request_key, actual)

  def test_request_key_to_task_to_run_key(self):
    task_key = task_to_run.request_key_to_task_to_run_key(
        task_request.id_to_request_key(256))
    expected = (
        "Key('TaskRequestShard', 'f71849', 'TaskRequest', 256, 'TaskToRun', 1)")
    self.assertEqual(expected, str(task_key))

  def test_validate_to_run_key(self):
    task_key = task_to_run.request_key_to_task_to_run_key(
        task_request.id_to_request_key(256))
    task_to_run.validate_to_run_key(task_key)
    with self.assertRaises(ValueError):
      task_to_run.validate_to_run_key(ndb.Key('TaskRequest', 1, 'TaskToRun', 1))

  def test_new_task_to_run(self):
    self.mock(task_request.random, 'getrandbits', lambda _: 0x12)
    request_dimensions = {u'OS': u'Windows-3.1.1'}
    data = _gen_request_data(
        properties={
          'commands': [[u'command1', u'arg1']],
          'data': [[u'http://localhost/foo', u'foo.zip']],
          'dimensions': request_dimensions,
          'env': {u'foo': u'bar'},
          'execution_timeout_secs': 30,
        },
        priority=20,
        scheduling_expiration_secs=31)
    task_to_run.new_task_to_run(task_request.make_request(data)).put()

    # Create a second with higher priority.
    self.mock(task_request.random, 'getrandbits', lambda _: 0x23)
    data = _gen_request_data(
        properties={
          'commands': [[u'command1', u'arg1']],
          'data': [[u'http://localhost/foo', u'foo.zip']],
          'dimensions': request_dimensions,
          'env': {u'foo': u'bar'},
          'execution_timeout_secs': 30,
        },
        priority=10,
        scheduling_expiration_secs=31)
    task_to_run.new_task_to_run(task_request.make_request(data)).put()

    expected = [
      {
        'dimensions_hash': _hash_dimensions(request_dimensions),
        'expiration_ts': self.now + datetime.timedelta(seconds=31),
        # random 8-15 bits = 23 as mocked above.
        'request_key': '0x014350e868882300',
        'key': '1',
        # Lower priority value means higher priority.
        'queue_number': '0x05014350e8688800',
      },
      {
        'dimensions_hash': _hash_dimensions(request_dimensions),
        'expiration_ts': self.now + datetime.timedelta(seconds=31),
        # random 8-15 bits = 12 as mocked above.
        'request_key': '0x014350e868881200',
        'key': '1',
        'queue_number': '0x0a014350e8688800',
      },
    ]

    def flatten(i):
      out = _task_to_run_to_dict(i)
      out['request_key'] = '0x%016x' % i.request_key.integer_id()
      out['key'] = '%x' % i.key.integer_id()
      return out

    # Warning: Ordering by key doesn't work because of TaskToRunShard; e.g.
    # the entity key ordering DOES NOT correlate with .queue_number
    # Ensure they come out in expected order.
    q = task_to_run.TaskToRun.query().order(task_to_run.TaskToRun.queue_number)
    self.assertEqual(expected, map(flatten, q.fetch()))

  def test_yield_next_available_task_to_dispatch_none(self):
    _gen_new_task_to_run(
        properties=dict(dimensions={u'OS': u'Windows-3.1.1'}))
    # Bot declares no dimensions, so it will fail to match.
    bot_dimensions = {}
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    self.assertEqual([], actual)

  def test_yield_next_available_task_to_dispatch_none_mismatch(self):
    _gen_new_task_to_run(
        properties=dict(dimensions={u'OS': u'Windows-3.1.1'}))
    # Bot declares other dimensions, so it will fail to match.
    bot_dimensions = {'OS': 'Windows-3.0'}
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    self.assertEqual([], actual)

  def test_yield_next_available_task_to_dispatch(self):
    request_dimensions = {
      u'OS': u'Windows-3.1.1', u'hostname': u'localhost', u'foo': u'bar',
    }
    _gen_new_task_to_run(
        properties=dict(dimensions=request_dimensions))
    # Bot declares exactly same dimensions so it matches.
    bot_dimensions = request_dimensions
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    expected = [
      {
        'dimensions_hash': _hash_dimensions(request_dimensions),
        'expiration_ts': self.expiration_ts,
        'queue_number': '0x19014350e8688800',
      },
    ]
    self.assertEqual(expected, actual)

  def test_yield_next_available_task_to_dispatch_subset(self):
    request_dimensions = {u'OS': u'Windows-3.1.1', u'foo': u'bar'}
    _gen_new_task_to_run(
        properties=dict(dimensions=request_dimensions))
    # Bot declares more dimensions than needed, this is fine and it matches.
    bot_dimensions = {
      u'OS': u'Windows-3.1.1', u'hostname': u'localhost', u'foo': u'bar',
    }
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    expected = [
      {
        'dimensions_hash': _hash_dimensions(request_dimensions),
        'expiration_ts': self.expiration_ts,
        'queue_number': '0x19014350e8688800',
      },
    ]
    self.assertEqual(expected, actual)

  def test_yield_next_available_task_shard(self):
    request_dimensions = {u'OS': u'Windows-3.1.1', u'foo': u'bar'}
    _gen_new_task_to_run(properties=dict(dimensions=request_dimensions))
    bot_dimensions = request_dimensions
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    expected = [
      {
        'dimensions_hash': _hash_dimensions(request_dimensions),
        'expiration_ts': self.expiration_ts,
        'queue_number': '0x19014350e8688800',
      },
    ]
    self.assertEqual(expected, actual)

  def test_yield_next_available_task_to_dispatch_subset_multivalue(self):
    request_dimensions = {u'OS': u'Windows-3.1.1', u'foo': u'bar'}
    _gen_new_task_to_run(
        properties=dict(dimensions=request_dimensions))
    # Bot declares more dimensions than needed.
    bot_dimensions = {
      u'OS': [u'Windows', u'Windows-3.1.1'],
      u'hostname': u'localhost',
      u'foo': u'bar',
    }
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    expected = [
      {
        'dimensions_hash': _hash_dimensions(request_dimensions),
        'expiration_ts': self.expiration_ts,
        'queue_number': '0x19014350e8688800',
      },
    ]
    self.assertEqual(expected, actual)

  def test_yield_next_available_task_to_dispatch_multi_normal(self):
    # Task added one after the other, normal case.
    request_dimensions_1 = {u'OS': u'Windows-3.1.1', u'foo': u'bar'}
    _gen_new_task_to_run(properties=dict(dimensions=request_dimensions_1))

    # It's normally time ordered.
    test_helper.mock_now(self, self.now + datetime.timedelta(seconds=1))
    request_dimensions_2 = {u'hostname': u'localhost'}
    _gen_new_task_to_run(properties=dict(dimensions=request_dimensions_2))

    bot_dimensions = {
      u'OS': u'Windows-3.1.1', u'hostname': u'localhost', u'foo': u'bar',
    }
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    expected = [
      {
        'dimensions_hash': _hash_dimensions(request_dimensions_1),
        'expiration_ts': self.expiration_ts,
        'queue_number': '0x19014350e8688800',
      },
      {
        'dimensions_hash': _hash_dimensions(request_dimensions_2),
        'expiration_ts': self.expiration_ts + datetime.timedelta(seconds=1),
        'queue_number': '0x19014350e86c7000',
      },
    ]
    self.assertEqual(expected, actual)

  def test_yield_next_available_task_to_dispatch_clock_skew(self):
    # Asserts that a TaskToRun added later in the DB (with a Key with an higher
    # value) but with a timestamp sooner (for example, time desynchronization
    # between machines) is still returned in the timestamp order, e.g. priority
    # is done based on timestamps and priority only.
    request_dimensions_1 = {u'OS': u'Windows-3.1.1', u'foo': u'bar'}
    _gen_new_task_to_run(properties=dict(dimensions=request_dimensions_1))

    # The second shard is added before the first, potentially because of a
    # desynchronized clock. It'll have higher priority.
    test_helper.mock_now(self, self.now - datetime.timedelta(seconds=1))
    request_dimensions_2 = {u'hostname': u'localhost'}
    _gen_new_task_to_run(properties=dict(dimensions=request_dimensions_2))

    bot_dimensions = {
      u'OS': u'Windows-3.1.1', u'hostname': u'localhost', u'foo': u'bar',
    }
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    expected = [
      {
        'dimensions_hash': _hash_dimensions(request_dimensions_2),
        # Due to time being late on the second requester frontend.
        'expiration_ts': self.expiration_ts - datetime.timedelta(seconds=1),
        'queue_number': '0x19014350e864a000',
      },
      {
        'dimensions_hash': _hash_dimensions(request_dimensions_1),
        'expiration_ts': self.expiration_ts,
        'queue_number': '0x19014350e8688800',
      },
    ]
    self.assertEqual(expected, actual)

  def test_yield_next_available_task_to_dispatch_priority(self):
    # Task added later but with higher priority are returned first.
    request_dimensions_1 = {u'OS': u'Windows-3.1.1'}
    _gen_new_task_to_run(properties=dict(dimensions=request_dimensions_1))

    # This one is later but has higher priority.
    test_helper.mock_now(self, self.now + datetime.timedelta(seconds=60))
    request_dimensions_2 = {u'OS': u'Windows-3.1.1'}
    _gen_new_task_to_run(
        properties=dict(dimensions=request_dimensions_2), priority=10)

    # It should return them all, in the expected order.
    expected = [
      {
        'dimensions_hash': _hash_dimensions(request_dimensions_1),
        'expiration_ts': datetime.datetime(2014, 1, 2, 3, 6, 5, 6),
        'queue_number': '0x05014350e952e800',
      },
      {
        'dimensions_hash': _hash_dimensions(request_dimensions_2),
        'expiration_ts': datetime.datetime(2014, 1, 2, 3, 5, 5, 6),
        'queue_number': '0x19014350e8688800',
      },
    ]
    bot_dimensions = {u'OS': u'Windows-3.1.1', u'hostname': u'localhost'}
    actual = _yield_next_available_task_to_dispatch(bot_dimensions)
    self.assertEqual(expected, actual)

  def test_yield_expired_task_to_run(self):
    _gen_new_task_to_run(scheduling_expiration_secs=60)
    self.assertEqual(1, len(_yield_next_available_task_to_dispatch({})))
    self.assertEqual(
        0, len(list(task_to_run.yield_expired_task_to_run())))

    # All tasks are now expired. Note that even if they still have .queue_number
    # set because the cron job wasn't run, they are still not yielded by
    # yield_next_available_task_to_dispatch()
    test_helper.mock_now(self, self.now + datetime.timedelta(seconds=61))
    self.assertEqual(0, len(_yield_next_available_task_to_dispatch({})))
    self.assertEqual(
        1, len(list(task_to_run.yield_expired_task_to_run())))

  def test_reap_task_to_run(self):
    to_run_1 = _gen_new_task_to_run(
        properties=dict(dimensions={u'OS': u'Windows-3.1.1'}))
    to_run_2 = _gen_new_task_to_run(
        properties=dict(dimensions={u'OS': u'Windows-3.1.1'}))
    bot_dimensions = {u'OS': u'Windows-3.1.1', u'hostname': u'localhost'}
    self.assertEqual(
        2, len(_yield_next_available_task_to_dispatch(bot_dimensions)))

    # A bot is assigned a task shard.
    self.assertEqual(
        True,
        ndb.transaction(lambda: task_to_run.reap_task_to_run(to_run_1.key)))
    self.assertEqual(
        1, len(_yield_next_available_task_to_dispatch(bot_dimensions)))

    # This task shard cannot be assigned anymore.
    self.assertEqual(
        False,
        ndb.transaction(lambda: task_to_run.reap_task_to_run(to_run_1.key)))
    self.assertEqual(
        1, len(_yield_next_available_task_to_dispatch(bot_dimensions)))
    self.assertEqual(
        True,
        ndb.transaction(lambda: task_to_run.reap_task_to_run(to_run_2.key)))
    self.assertEqual(
        0, len(_yield_next_available_task_to_dispatch(bot_dimensions)))

  def test_retry_task_to_run(self):
    to_run_1 = _gen_new_task_to_run(
        properties=dict(dimensions={u'OS': u'Windows-3.1.1'}))
    to_run_2 = _gen_new_task_to_run(
        properties=dict(dimensions={u'OS': u'Windows-3.1.1'}))
    bot_dimensions = {u'OS': u'Windows-3.1.1', u'hostname': u'localhost'}
    # Grabs the 2 available tasks so no task is available afterward.
    ndb.transaction(lambda: task_to_run.reap_task_to_run(to_run_1.key))
    ndb.transaction(lambda: task_to_run.reap_task_to_run(to_run_2.key))
    self.assertEqual(
        0, len(_yield_next_available_task_to_dispatch(bot_dimensions)))

    # Calling retry_task_to_run() puts the task back as available for
    # dispatch.
    self.assertIs(True, task_to_run.retry_task_to_run(to_run_1.key))
    self.assertEqual(
        1, len(_yield_next_available_task_to_dispatch(bot_dimensions)))

    # Can't retry a task already available to be dispatched to a bot.
    self.assertEqual(
        False, task_to_run.retry_task_to_run(to_run_1.key))
    self.assertEqual(
        1, len(_yield_next_available_task_to_dispatch(bot_dimensions)))

    # Retry the second task too.
    self.assertEqual(
        True, task_to_run.retry_task_to_run(to_run_2.key))
    self.assertEqual(
        2, len(_yield_next_available_task_to_dispatch(bot_dimensions)))

  def test_abort_task_to_run(self):
    task = _gen_new_task_to_run(
        properties=dict(dimensions={u'OS': u'Windows-3.1.1'}))
    _gen_new_task_to_run(
        properties=dict(dimensions={u'OS': u'Windows-3.1.1'}))
    bot_dimensions = {u'OS': u'Windows-3.1.1', u'hostname': u'localhost'}
    self.assertEqual(
        2, len(_yield_next_available_task_to_dispatch(bot_dimensions)))
    task_to_run.abort_task_to_run(task)
    self.assertEqual(
        1, len(_yield_next_available_task_to_dispatch(bot_dimensions)))

    # Aborting an aborted task shard is just fine.
    task_to_run.abort_task_to_run(task)
    self.assertEqual(
        1, len(_yield_next_available_task_to_dispatch(bot_dimensions)))


if __name__ == '__main__':
  if '-v' in sys.argv:
    unittest.TestCase.maxDiff = None
  unittest.main()
