#!/usr/bin/env python
# Copyright 2013 The LUCI Authors. All rights reserved.
# Use of this source code is governed under the Apache License, Version 2.0
# that can be found in the LICENSE file.

import datetime
import hashlib
import logging
import os
import sys
import unittest
import zlib

import test_env
test_env.setup_test_env()

import cloudstorage
from google.appengine.ext import ndb

import webtest

from components import auth
from components import auth_testing
from components import template
from components import utils
from test_support import test_case

import acl
import config
import gcs
import handlers_backend
import handlers_frontend
import model
import stats

# Access to a protected member _XXX of a client class
# pylint: disable=W0212


class MainTest(test_case.TestCase):
  """Tests the handlers."""
  APP_DIR = test_env.APP_DIR

  def setUp(self):
    """Creates a new app instance for every test case."""
    super(MainTest, self).setUp()
    self.testbed.init_user_stub()

    # When called during a taskqueue, the call to get_app_version() may fail so
    # pre-fetch it.
    version = utils.get_app_version()
    self.mock(utils, 'get_task_queue_host', lambda: version)
    self.source_ip = '192.168.0.1'
    self.app_frontend = webtest.TestApp(
        handlers_frontend.create_application(debug=True),
        extra_environ={'REMOTE_ADDR': self.source_ip})
    # This is awkward but both the frontend and backend applications uses the
    # same template variables.
    template.reset()
    self.app_backend = webtest.TestApp(
        handlers_backend.create_application(debug=True),
        extra_environ={'REMOTE_ADDR': self.source_ip})
    # Tasks are enqueued on the backend.
    self.app = self.app_backend

    self.auth_app = webtest.TestApp(
        auth.create_wsgi_application(debug=True),
        extra_environ={
          'REMOTE_ADDR': self.source_ip,
          'SERVER_SOFTWARE': os.environ['SERVER_SOFTWARE'],
        })

    full_access_group = config.settings().auth.full_access_group
    readonly_access_group = config.settings().auth.readonly_access_group

    auth.bootstrap_group(
        auth.ADMIN_GROUP,
        [auth.Identity(auth.IDENTITY_USER, 'admin@example.com')])
    auth.bootstrap_group(
        readonly_access_group,
        [auth.Identity(auth.IDENTITY_USER, 'reader@example.com')])
    auth.bootstrap_group(
        full_access_group,
        [auth.Identity(auth.IDENTITY_USER, 'writer@example.com')])
    # TODO(maruel): Create a BOTS_GROUP.

    self.set_as_anonymous()

  def tearDown(self):
    template.reset()
    super(MainTest, self).tearDown()

  def set_as_anonymous(self):
    self.testbed.setup_env(USER_EMAIL='', overwrite=True)
    auth.ip_whitelist_key(auth.bots_ip_whitelist()).delete()
    auth_testing.reset_local_state()

  def set_as_admin(self):
    self.set_as_anonymous()
    self.testbed.setup_env(USER_EMAIL='admin@example.com', overwrite=True)

  def set_as_reader(self):
    self.set_as_anonymous()
    self.testbed.setup_env(USER_EMAIL='reader@example.com', overwrite=True)

  def _gen_stats(self):
    # Generates data for the last 10 days, last 10 hours and last 10 minutes.
    # TODO(maruel): Stop accessing the DB directly. Use stats_framework_mock to
    # generate it.
    now = datetime.datetime(2010, 1, 2, 3, 4, 5, 6)
    self.mock_now(now, 0)
    handler = stats.STATS_HANDLER
    for i in xrange(10):
      s = stats._Snapshot(requests=100 + i)
      day = (now - datetime.timedelta(days=i)).date()
      handler.stats_day_cls(key=handler.day_key(day), values_compressed=s).put()

    for i in xrange(10):
      s = stats._Snapshot(requests=10 + i)
      timestamp = (now - datetime.timedelta(hours=i))
      handler.stats_hour_cls(
          key=handler.hour_key(timestamp), values_compressed=s).put()

    for i in xrange(10):
      s = stats._Snapshot(requests=1 + i)
      timestamp = (now - datetime.timedelta(minutes=i))
      handler.stats_minute_cls(
          key=handler.minute_key(timestamp), values_compressed=s).put()

  @staticmethod
  def gen_content_inline(namespace='default', content='Foo'):
    hashhex = hashlib.sha1(content).hexdigest()
    key = model.get_entry_key(namespace, hashhex)
    model.new_content_entry(
        key,
        is_isolated=False,
        content=content,
        compressed_size=len(content),
        expanded_size=len(content),
        is_verified=True).put()
    return hashhex

  def get_xsrf_token(self):
    """Gets the generic XSRF token for web clients."""
    resp = self.auth_app.post(
        '/auth/api/v1/accounts/self/xsrf_token',
        headers={'X-XSRF-Token-Request': '1'}).json
    return resp['xsrf_token'].encode('ascii')

  def test_root(self):
    # Just asserts it doesn't crash.
    self.app_frontend.get('/')

  def test_browse(self):
    self.set_as_reader()
    hashhex = self.gen_content_inline()
    self.app_frontend.get('/browse?namespace=default&digest=%s' % hashhex)
    self.app_frontend.get(
      '/browse?namespace=default&digest=%s&as=file1.txt' % hashhex)

  def test_browse_missing(self):
    self.set_as_reader()
    hashhex = '0123456780123456780123456789990123456789'
    self.app_frontend.get(
      '/browse?namespace=default&digest=%s' % hashhex, status=404)

  def test_content(self):
    self.set_as_reader()
    hashhex = self.gen_content_inline(content='Foo')
    resp = self.app_frontend.get(
        '/content?namespace=default&digest=%s' % hashhex)
    self.assertEqual('Foo', resp.body)
    resp = self.app_frontend.get(
        '/content?namespace=default&digest=%s&as=file1.txt' % hashhex)
    self.assertEqual('Foo', resp.body)

  def test_content_gcs(self):
    content = 'Foo'
    compressed = zlib.compress(content)
    namespace = 'default-gzip'
    hashhex = hashlib.sha1(content).hexdigest()

    def read_file(bucket, key):
      self.assertEqual(u'sample-app', bucket)
      self.assertEqual(namespace + '/' + hashhex, key)
      return [compressed]
    self.mock(gcs, 'read_file', read_file)

    key = model.get_entry_key(namespace, hashhex)
    model.new_content_entry(
        key,
        is_isolated=False,
        compressed_size=len(compressed),
        expanded_size=len(content),
        is_verified=True).put()

    self.set_as_reader()
    resp = self.app_frontend.get(
        '/content?namespace=default-gzip&digest=%s' % hashhex)
    self.assertEqual(content, resp.body)
    resp = self.app_frontend.get(
      '/content?namespace=default-gzip&digest=%s&as=file1.txt' % hashhex)
    self.assertEqual(content, resp.body)
    self.assertNotEqual(None, key.get())

  def test_content_gcs_missing(self):
    content = 'Foo'
    compressed = zlib.compress(content)
    namespace = 'default-gzip'
    hashhex = hashlib.sha1(content).hexdigest()

    def read_file(bucket, key):
      self.assertEqual(u'sample-app', bucket)
      self.assertEqual(namespace + '/' + hashhex, key)
      raise cloudstorage.NotFoundError('Someone deleted the file from GCS')
    self.mock(gcs, 'read_file', read_file)

    key = model.get_entry_key(namespace, hashhex)
    model.new_content_entry(
        key,
        is_isolated=False,
        compressed_size=len(compressed),
        expanded_size=len(content),
        is_verified=True).put()

    self.set_as_reader()
    self.app_frontend.get(
        '/content?namespace=default-gzip&digest=%s' % hashhex, status=404)
    self.assertEqual(None, key.get())

  def test_config(self):
    self.set_as_admin()
    resp = self.app_frontend.get('/restricted/config')
    # TODO(maruel): Use beautifulsoup?
    priv_key = 'test private key'
    params = {
      'gs_private_key': priv_key,
      'keyid': str(config.settings_info()['cfg'].key.integer_id()),
      'xsrf_token': self.get_xsrf_token(),
    }
    self.assertEqual('', config.settings().gs_private_key)
    resp = self.app_frontend.post('/restricted/config', params)
    self.assertNotIn('Update conflict', resp)
    self.assertEqual(priv_key, config.settings().gs_private_key)

  def test_config_conflict(self):
    self.set_as_admin()
    resp = self.app_frontend.get('/restricted/config')
    # TODO(maruel): Use beautifulsoup?
    params = {
      'google_analytics': 'foobar',
      'keyid': str(config.settings().key.integer_id() - 1),
      'reusable_task_age_secs': 30,
      'xsrf_token': self.get_xsrf_token(),
    }
    self.assertEqual('', config.settings().google_analytics)
    resp = self.app_frontend.post('/restricted/config', params)
    self.assertIn('Update conflict', resp)
    self.assertEqual('', config.settings().google_analytics)

  def test_stats(self):
    self._gen_stats()
    response = self.app_frontend.get('/stats')
    # Just ensure something is returned.
    self.assertGreater(response.content_length, 4000)

  def test_api_stats_days(self):
    self._gen_stats()
    # It's cheezy but at least it asserts that the data makes sense.
    expected = (
        'google.visualization.Query.setResponse({"status":"ok","table":{"rows":'
        '[{"c":[{"v":"Date(2010,0,2)"},{"v":100},{"v":100},{"v":0},{"v":0},{"v"'
        ':0},{"v":0},{"v":0},{"v":0},{"v":0}]}],"cols":[{"type":"date","id":"ke'
        'y","label":"Day"},{"type":"number","id":"requests","label":"Total"},{"'
        'type":"number","id":"other_requests","label":"Other"},{"type":"number"'
        ',"id":"failures","label":"Failures"},{"type":"number","id":"uploads","'
        'label":"Uploads"},{"type":"number","id":"downloads","label":"Downloads'
        '"},{"type":"number","id":"contains_requests","label":"Lookups"},{"type'
        '":"number","id":"uploads_bytes","label":"Uploaded"},{"type":"number","'
        'id":"downloads_bytes","label":"Downloaded"},{"type":"number","id":"con'
        'tains_lookups","label":"Items looked up"}]},"reqId":"0","version":"0.6'
        '"});')
    response = self.app_frontend.get('/isolate/api/v1/stats/days?duration=1')
    self.assertEqual(expected, response.body)

  def test_api_stats_hours(self):
    self._gen_stats()
    # It's cheezy but at least it asserts that the data makes sense.
    expected = (
        'google.visualization.Query.setResponse({"status":"ok","table":{"rows":'
        '[{"c":[{"v":"Date(2010,0,2,3,0,0)"},{"v":10},{"v":10},{"v":0},{"v":0},'
        '{"v":0},{"v":0},{"v":0},{"v":0},{"v":0}]}],"cols":[{"type":"datetime",'
        '"id":"key","label":"Time"},{"type":"number","id":"requests","label":"T'
        'otal"},{"type":"number","id":"other_requests","label":"Other"},{"type"'
        ':"number","id":"failures","label":"Failures"},{"type":"number","id":"u'
        'ploads","label":"Uploads"},{"type":"number","id":"downloads","label":"'
        'Downloads"},{"type":"number","id":"contains_requests","label":"Lookups'
        '"},{"type":"number","id":"uploads_bytes","label":"Uploaded"},{"type":"'
        'number","id":"downloads_bytes","label":"Downloaded"},{"type":"number",'
        '"id":"contains_lookups","label":"Items looked up"}]},"reqId":"0","vers'
        'ion":"0.6"});')
    response = self.app_frontend.get(
        '/isolate/api/v1/stats/hours?duration=1&now=')
    self.assertEqual(expected, response.body)

  def test_api_stats_minutes(self):
    self._gen_stats()
    # It's cheezy but at least it asserts that the data makes sense.
    expected = (
        'google.visualization.Query.setResponse({"status":"ok","table":{"rows":'
        '[{"c":[{"v":"Date(2010,0,2,3,4,0)"},{"v":1},{"v":1},{"v":0},{"v":0},{"'
        'v":0},{"v":0},{"v":0},{"v":0},{"v":0}]}],"cols":[{"type":"datetime","i'
        'd":"key","label":"Time"},{"type":"number","id":"requests","label":"Tot'
        'al"},{"type":"number","id":"other_requests","label":"Other"},{"type":"'
        'number","id":"failures","label":"Failures"},{"type":"number","id":"upl'
        'oads","label":"Uploads"},{"type":"number","id":"downloads","label":"Do'
        'wnloads"},{"type":"number","id":"contains_requests","label":"Lookups"}'
        ',{"type":"number","id":"uploads_bytes","label":"Uploaded"},{"type":"nu'
        'mber","id":"downloads_bytes","label":"Downloaded"},{"type":"number","i'
        'd":"contains_lookups","label":"Items looked up"}]},"reqId":"0","versio'
        'n":"0.6"});')
    response = self.app_frontend.get('/isolate/api/v1/stats/minutes?duration=1')
    self.assertEqual(expected, response.body)


if __name__ == '__main__':
  if '-v' in sys.argv:
    unittest.TestCase.maxDiff = None
    logging.basicConfig(level=logging.DEBUG)
  else:
    logging.basicConfig(level=logging.FATAL)
  unittest.main()
