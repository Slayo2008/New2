#!/usr/bin/python
# Copyright 2016 The LUCI Authors. All rights reserved.
# Use of this source code is governed under the Apache License, Version 2.0
# that can be found in the LICENSE file.

"""Unit tests for instances.py."""

import unittest

import test_env
test_env.setup_test_env()

from google.appengine.ext import ndb

from components import net
from components import utils
from test_support import test_case

import instance_group_managers
import instances
import models


class AddLeaseExpirationTsTest(test_case.TestCase):
  """Tests for instances.add_lease_expiration_ts."""

  def test_entity_not_found(self):
    """Ensures nothing happens when the entity doesn't exist."""
    key = ndb.Key(models.Instance, 'fake-instance')

    instances.add_lease_expiration_ts(key, utils.utcnow())

    self.failIf(key.get())

  def test_lease_expiration_ts_added(self):
    now = utils.utcnow()
    key = models.Instance(
        key=instances.get_instance_key(
            'base-name',
            'revision',
            'zone',
            'instance-name',
        ),
    ).put()

    instances.add_lease_expiration_ts(key, now)

    self.assertEqual(key.get().lease_expiration_ts, now)
    self.failUnless(key.get().leased)

  def test_lease_expiration_ts_matches(self):
    now = utils.utcnow()
    key = models.Instance(
        key=instances.get_instance_key(
            'base-name',
            'revision',
            'zone',
            'instance-name',
        ),
        lease_expiration_ts=now,
    ).put()

    instances.add_lease_expiration_ts(key, now)

    self.assertEqual(key.get().lease_expiration_ts, now)
    self.failUnless(key.get().leased)

  def test_lease_expiration_ts_updated(self):
    now = utils.utcnow()
    key = models.Instance(
        key=instances.get_instance_key(
            'base-name',
            'revision',
            'zone',
            'instance-name',
        ),
        lease_expiration_ts=utils.utcnow(),
    ).put()

    instances.add_lease_expiration_ts(key, now)

    self.assertEqual(key.get().lease_expiration_ts, now)
    self.failUnless(key.get().leased)


class AddSubscriptionMetadataTest(test_case.TestCase):
  """Tests for instances.add_subscription_metadata."""

  def test_entity_not_found(self):
    """Ensures nothing happens when the entity doesn't exist."""
    key = ndb.Key(models.Instance, 'fake-instance')

    instances.add_subscription_metadata(
        key, 'project', 'subscription', 'service-account')

    self.failIf(key.get())

  def test_pubsub_subscription_specified(self):
    """Ensures nothing happens when the entity already has a subscription."""
    key = models.Instance(
        key=instances.get_instance_key(
            'base-name',
            'revision',
            'zone',
            'instance-name',
        ),
        pubsub_subscription='original-subscription',
    ).put()

    instances.add_subscription_metadata(
        key, 'project', 'new-subscription', 'service-account')

    self.failIf(key.get().pending_metadata_updates)
    self.failIf(key.get().pubsub_service_account)
    self.assertEqual(key.get().pubsub_subscription, 'original-subscription')

  def test_metadata_added(self):
    """Ensures metadata update is scheduled."""
    key = models.Instance(
        key=instances.get_instance_key(
            'base-name',
            'revision',
            'zone',
            'instance-name',
        ),
    ).put()

    instances.add_subscription_metadata(
        key, 'project', 'subscription', 'service-account')

    self.failUnless(key.get().pending_metadata_updates)
    self.assertEqual(key.get().pubsub_service_account, 'service-account')
    self.assertEqual(
        key.get().pubsub_subscription,
        'projects/project/subscriptions/subscription',
    )


class DeleteDrainedTest(test_case.TestCase):
  """Tests for instances.delete_drained."""

  def test_entity_not_found(self):
    """Ensures nothing happens when the entity is not found."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    self.mock(instances.net, 'json_request', json_request)

    key = ndb.Key(models.Instance, 'fake-key')

    instances.delete_drained(key)

    self.failIf(key.get())

  def test_cataloged(self):
    """Ensures nothing happens when the entity is cataloged."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    self.mock(instances.net, 'json_request', json_request)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        cataloged=True,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()
    models.InstanceTemplate(
        key=instances.get_instance_group_manager_key(key).parent().parent(),
    ).put()

    instances.delete_drained(key)

  def test_url_unspecified(self):
    """Ensures nothing happens when the entity has no URL."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    self.mock(instances.net, 'json_request', json_request)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()
    models.InstanceTemplate(
        key=instances.get_instance_group_manager_key(key).parent().parent(),
    ).put()

    instances.delete_drained(key)

  def test_parent_doesnt_exist(self):
    """Ensures nothing happens when the parent is unspecified."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    self.mock(instances.net, 'json_request', json_request)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()
    models.InstanceTemplate(
        key=instances.get_instance_group_manager_key(key).parent().parent(),
    ).put()

    instances.delete_drained(key)

    self.failIf(key.get().deleted)

  def test_grandparent_doesnt_exist(self):
    """Ensures nothing happens when the grandparent is unspecified."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    self.mock(instances.net, 'json_request', json_request)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplate(
        key=instances.get_instance_group_manager_key(key).parent().parent(),
    ).put()

    instances.delete_drained(key)

  def test_project_unspecified(self):
    """Ensures nothing happens when the project is unspecified."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    self.mock(instances.net, 'json_request', json_request)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
    ).put()
    models.InstanceTemplate(
        key=instances.get_instance_group_manager_key(key).parent().parent(),
    ).put()

    instances.delete_drained(key)

  def test_root_doesnt_exist(self):
    """Ensures nothing happens when the root is unspecified."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    self.mock(instances.net, 'json_request', json_request)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()

    instances.delete_drained(key)

  def test_not_drained(self):
    """Ensures nothing happens when the parent is not drained."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    self.mock(instances.net, 'json_request', json_request)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()
    models.InstanceTemplate(
        key=instances.get_instance_group_manager_key(key).parent().parent(),
    ).put()

    instances.delete_drained(key)

  def test_implicitly_drained(self):
    """Ensures the instance is deleted when the parent is implicitly drained."""
    def json_request(*args, **kwargs):
      return {'status': 'DONE'}
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()
    models.InstanceTemplate(
        key=instances.get_instance_group_manager_key(key).parent().parent(),
        drained=[
            instances.get_instance_group_manager_key(key).parent(),
        ],
    ).put()

    instances.delete_drained(key)

  def test_drained(self):
    """Ensures the instance is deleted when the parent is drained."""
    def json_request(*args, **kwargs):
      return {'status': 'DONE'}
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        drained=[
            instances.get_instance_group_manager_key(key),
        ],
        project='project',
    ).put()
    models.InstanceTemplate(
        key=instances.get_instance_group_manager_key(key).parent().parent(),
    ).put()

    instances.delete_drained(key)


class DeletePendingTest(test_case.TestCase):
  """Tests for instances.delete_pending."""

  def test_entity_doesnt_exist(self):
    """Ensures nothing happens when the entity doesn't exist."""
    key = ndb.Key(models.Instance, 'fake-instance')

    instances.delete_pending(key)

    self.failIf(key.get())

  def test_not_pending_deletion(self):
    """Ensures nothing happens when the instance isn't pending deletion."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    def send_machine_event(*args, **kwargs):
      self.fail('send_machine_event called')
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=False,
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()

    instances.delete_pending(key)

  def test_url_unspecified(self):
    """Ensures nothing happens when the URL doesn't exist."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    def send_machine_event(*args, **kwargs):
      self.fail('send_machine_event called')
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=True,
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()

    instances.delete_pending(key)

  def test_parent_unspecified(self):
    """Ensures nothing happens when the parent doesn't exist."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    def send_machine_event(*args, **kwargs):
      self.fail('send_machine_event called')
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=True,
        url='url',
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()

    instances.delete_pending(key)

  def test_grandparent_unspecified(self):
    """Ensures nothing happens when the grandparent doesn't exist."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    def send_machine_event(*args, **kwargs):
      self.fail('send_machine_event called')
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=True,
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()

    instances.delete_pending(key)

  def test_project_unspecified(self):
    """Ensures nothing happens when the project is unspecified."""
    def json_request(*args, **kwargs):
      self.fail('json_request called')
    def send_machine_event(*args, **kwargs):
      self.fail('send_machine_event called')
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=True,
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
    ).put()

    instances.delete_pending(key)

  def test_deleted(self):
    """Ensures instance is deleted."""
    def json_request(*args, **kwargs):
      return {'status': 'DONE'}
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=True,
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()

    instances.delete_pending(key)

  def test_deleted_not_done(self):
    """Ensures nothing happens when instance deletion status is not DONE."""
    def json_request(*args, **kwargs):
      return {'status': 'error'}
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=True,
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()

    instances.delete_pending(key)

  def test_already_deleted(self):
    """Ensures errors are ignored when the instance is already deleted."""
    def json_request(*args, **kwargs):
      raise net.Error('400', 400, '400')
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=True,
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()

    instances.delete_pending(key)

  def test_error_surfaced(self):
    """Ensures errors are surfaced."""
    def json_request(*args, **kwargs):
      raise net.Error('403', 403, '403')
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances.net, 'json_request', json_request)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    key = models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
        pending_deletion=True,
        url='url',
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceTemplateRevision(
        key=instances.get_instance_group_manager_key(key).parent(),
        project='project',
    ).put()

    self.assertRaises(net.Error, instances.delete_pending, key)


class GetInstanceGroupManagerKeyTest(test_case.TestCase):
  """Tests for instances.get_instance_group_manager_key."""

  def test_equal(self):
    expected = instance_group_managers.get_instance_group_manager_key(
        'base-name',
        'revision',
        'zone',
    )

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )

    self.assertEqual(instances.get_instance_group_manager_key(key), expected)


class EnsureEntityExistsTest(test_case.TestCase):
  """Tests for instances.ensure_entity_exists."""

  def test_creates(self):
    """Ensures entity is created when it doesn't exist."""
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )
    expected_url = 'url'

    future = instances.ensure_entity_exists(
        key,
        expected_url,
        instances.get_instance_group_manager_key(key),
    )
    future.wait()

    self.assertEqual(key.get().url, expected_url)

  def test_entity_exists(self):
    """Ensures nothing happens when the entity already exists."""
    key = models.Instance(
        key=instances.get_instance_key(
            'base-name',
            'revision',
            'zone',
            'instance-name',
        ),
    ).put()

    future = instances.ensure_entity_exists(
        key, 'url', instances.get_instance_group_manager_key(key))
    future.wait()

    self.failIf(key.get().url)

  def test_entity_not_put(self):
    """Ensures nothing happens when the entity wasn't put."""
    @ndb.tasklet
    def _ensure_entity_exists(*args, **kwargs):
      raise ndb.Return(False)
    def send_machine_event(*args, **kwargs):
      self.fail('send_machine_event called')
    self.mock(instances, '_ensure_entity_exists', _ensure_entity_exists)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'instance-name',
    )

    future = instances.ensure_entity_exists(
        key, 'url', instances.get_instance_group_manager_key(key))
    future.wait()

    self.failIf(key.get())


class EnsureEntitiesExistTest(test_case.TestCase):
  """Tests for instances.ensure_entities_exist."""

  def test_entity_doesnt_exist(self):
    """Ensures nothing happens when the entity doesn't exist."""
    key = ndb.Key(models.InstanceGroupManager, 'fake-key')

    instances.ensure_entities_exist(key)
    self.failIf(key.get())

  def test_url_unspecified(self):
    """Ensures nothing happens when URL is unspecified."""
    key = models.InstanceGroupManager(
        key=instance_group_managers.get_instance_group_manager_key(
            'base-name',
            'revision',
            'zone',
        ),
    ).put()

    instances.ensure_entities_exist(key)
    self.failIf(key.get().instances)

  def test_no_instances(self):
    """Ensures nothing happens when there are no instances."""
    def fetch(*args, **kwargs):
      return []
    self.mock(instances, 'fetch', fetch)

    key = models.InstanceGroupManager(
        key=instance_group_managers.get_instance_group_manager_key(
            'base-name',
            'revision',
            'zone',
        ),
        url='url',
    ).put()

    instances.ensure_entities_exist(key)
    self.failIf(key.get().instances)

  def test_already_exists(self):
    """Ensures nothing happens when the entity already exists."""
    def fetch(*args, **kwargs):
      return ['url/name']
    self.mock(instances, 'fetch', fetch)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'name',
    )
    models.Instance(
        key=key,
        instance_group_manager=instances.get_instance_group_manager_key(key),
    ).put()
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    expected_instances = [
        key,
    ]

    instances.ensure_entities_exist(
        instances.get_instance_group_manager_key(key))
    self.failIf(key.get().url)
    self.assertItemsEqual(
        instances.get_instance_group_manager_key(key).get().instances,
        expected_instances,
    )

  def test_creates(self):
    """Ensures entity gets created."""
    def fetch(*args, **kwargs):
      return ['url/name']
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances, 'fetch', fetch)
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = instances.get_instance_key(
        'base-name',
        'revision',
        'zone',
        'name',
    )
    models.InstanceGroupManager(
        key=instances.get_instance_group_manager_key(key),
        url='url',
    ).put()
    expected_instances = [
        key,
    ]
    expected_url = 'url/name'

    instances.ensure_entities_exist(
        instances.get_instance_group_manager_key(key))
    self.assertItemsEqual(
        instances.get_instance_group_manager_key(key).get().instances,
        expected_instances,
    )
    self.assertEqual(key.get().url, expected_url)


class FetchTest(test_case.TestCase):
  """Tests for instances.fetch."""

  def test_entity_doesnt_exist(self):
    """Ensures nothing happens when the entity doesn't exist."""
    key = ndb.Key(models.InstanceGroupManager, 'fake-key')
    urls = instances.fetch(key)
    self.failIf(urls)

  def test_url_unspecified(self):
    """Ensures nothing happens when URL is unspecified."""
    key = models.InstanceGroupManager(
        key=instance_group_managers.get_instance_group_manager_key(
            'base-name',
            'revision',
            'zone',
        ),
    ).put()
    models.InstanceTemplateRevision(key=key.parent(), project='project').put()

    urls = instances.fetch(key)
    self.failIf(urls)

  def test_parent_doesnt_exist(self):
    """Ensures nothing happens when the parent doesn't exist."""
    key = models.InstanceGroupManager(
        key=instance_group_managers.get_instance_group_manager_key(
            'base-name',
            'revision',
            'zone',
        ),
        url='url',
    ).put()

    urls = instances.fetch(key)
    self.failIf(urls)

  def test_parent_project_unspecified(self):
    """Ensures nothing happens when parent doesn't specify a project."""
    key = models.InstanceGroupManager(
        key=instance_group_managers.get_instance_group_manager_key(
            'base-name',
            'revision',
            'zone',
        ),
        url='url',
    ).put()
    models.InstanceTemplateRevision(key=key.parent()).put()

    urls = instances.fetch(key)
    self.failIf(urls)

  def test_no_instances(self):
    """Ensures nothing happens when there are no instances."""
    def get_instances_in_instance_group(*args, **kwargs):
      return {}
    self.mock(
        instances.gce.Project,
        'get_instances_in_instance_group',
        get_instances_in_instance_group,
    )

    key = models.InstanceGroupManager(
        key=instance_group_managers.get_instance_group_manager_key(
            'base-name',
            'revision',
            'zone',
        ),
        url='url',
    ).put()
    models.InstanceTemplateRevision(key=key.parent(), project='project').put()

    urls = instances.fetch(key)
    self.failIf(urls)

  def test_instances(self):
    """Ensures instances are returned."""
    def get_instances_in_instance_group(*args, **kwargs):
      return {
          'instanceGroup': 'instance-group-url',
          'items': [
              {'instance': 'url/instance'},
          ],
      }
    self.mock(
        instances.gce.Project,
        'get_instances_in_instance_group',
        get_instances_in_instance_group,
    )

    key = models.InstanceGroupManager(
        key=instance_group_managers.get_instance_group_manager_key(
            'base-name',
            'revision',
            'zone',
        ),
        url='url',
    ).put()
    models.InstanceTemplateRevision(key=key.parent(), project='project').put()
    expected_urls = ['url/instance']

    urls = instances.fetch(key)
    self.assertItemsEqual(urls, expected_urls)

  def test_instances_with_page_token(self):
    """Ensures all instances are returned."""
    def get_instances_in_instance_group(*args, **kwargs):
      if kwargs.get('page_token'):
        return {
            'items': [
                {'instance': 'url/instance-2'},
            ],
        }
      return {
          'items': [
              {'instance': 'url/instance-1'},
          ],
          'nextPageToken': 'page-token',
      }
    self.mock(
        instances.gce.Project,
        'get_instances_in_instance_group',
        get_instances_in_instance_group,
    )

    key = models.InstanceGroupManager(
        key=instance_group_managers.get_instance_group_manager_key(
            'base-name',
            'revision',
            'zone',
        ),
        url='url',
    ).put()
    models.InstanceTemplateRevision(key=key.parent(), project='project').put()
    expected_urls = ['url/instance-1', 'url/instance-2']

    urls = instances.fetch(key)
    self.assertItemsEqual(urls, expected_urls)


class MarkForDeletionTest(test_case.TestCase):
  """Tests for instances.mark_for_deletion."""

  def test_entity_not_found(self):
    """Ensures nothing happens when the entity doesn't exist."""
    def send_machine_event(*args, **kwargs):
      self.fail('send_machine_event called')
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = ndb.Key(models.Instance, 'fake-instance')

    instances.mark_for_deletion(key)

    self.failIf(key.get())

  def test_marked(self):
    """Ensures the entity can be marked for deletion."""
    def send_machine_event(*args, **kwargs):
      pass
    self.mock(instances.metrics, 'send_machine_event', send_machine_event)

    key = models.Instance(
        key=instances.get_instance_key(
            'base-name',
            'revision',
            'zone',
            'instance-name',
        ),
    ).put()

    instances.mark_for_deletion(key)

    self.failUnless(key.get().pending_deletion)


if __name__ == '__main__':
  unittest.main()
