# Copyright 2015 The LUCI Authors. All rights reserved.
# Use of this source code is governed under the Apache License, Version 2.0
# that can be found in the LICENSE file.

"""Google Compute Engine specific utility functions."""

import json
import logging
import socket
import threading
import time
import urllib2

from utils import tools


### Private stuff.


# Cache of GCE OAuth2 token.
_CACHED_OAUTH2_TOKEN = {}
_CACHED_OAUTH2_TOKEN_LOCK = threading.Lock()


## Public API.


@tools.cached
def is_gce():
  """Returns True if running on Google Compute Engine."""
  # Attempting to hit the metadata server is unreliable since it sometimes
  # doesn't respond. Resolving DNS name is more stable, since it actually just
  # looks into /etc/hosts file (at least on Linux).
  #
  # See also https://github.com/GoogleCloudPlatform/gcloud-golang/issues/194.
  try:
    # 169.254.169.254 is the documented metadata server IP address.
    return socket.gethostbyname('metadata.google.internal') == '169.254.169.254'
  except socket.error:
    return False


def get_metadata_uncached():
  """Returns the GCE metadata as a dict.

  Returns None if not running on GCE or if metadata server is unreachable.

  Refs:
    https://cloud.google.com/compute/docs/metadata
    https://cloud.google.com/compute/docs/machine-types

  To get the at the command line from a GCE VM, use:
    curl --silent \
      http://metadata.google.internal/computeMetadata/v1/?recursive=true \
      -H "Metadata-Flavor: Google" | python -m json.tool | less
  """
  if not is_gce():
    logging.info('GCE metadata is not available: not on GCE')
    return None
  url = 'http://metadata.google.internal/computeMetadata/v1/?recursive=true'
  headers = {'Metadata-Flavor': 'Google'}
  for i in xrange(0, 10):
    if i:
      time.sleep(5)
    try:
      return json.load(
          urllib2.urlopen(urllib2.Request(url, headers=headers), timeout=10))
    except IOError as e:
      logging.warning('Failed to grab GCE metadata: %s', e)
  return None


# One-tuple that contains cached metadata, as returned by get_metadata.
_CACHED_METADATA = None


def wait_for_metadata(quit_bit):
  """Spins until GCE metadata server responds.

  Precaches value of 'get_metadata'.

  Args:
    quit_bit: its 'is_set' method is used to break from the loop earlier.
  """
  global _CACHED_METADATA
  if not is_gce():
    return
  while not quit_bit.is_set():
    meta = get_metadata_uncached()
    if meta is not None:
      _CACHED_METADATA = (meta,)
      break


def get_metadata():
  """Cached version of get_metadata_uncached()"""
  global _CACHED_METADATA
  if _CACHED_METADATA is not None:
    return _CACHED_METADATA[0]
  meta = get_metadata_uncached()
  # Don't cache 'None' reply on GCE. It is wrong. Better to try to grab the
  # right one next time.
  if meta is not None or not is_gce():
    _CACHED_METADATA = (meta,)
    return meta
  return None


def oauth2_access_token(account='default'):
  """Returns a value of oauth2 access token."""
  # TODO(maruel): Move GCE VM authentication logic into client/utils/net.py.
  # As seen in google-api-python-client/oauth2client/gce.py
  with _CACHED_OAUTH2_TOKEN_LOCK:
    cached_tok = _CACHED_OAUTH2_TOKEN.get(account)
    # Cached and expires in more than 5 min from now.
    if cached_tok and cached_tok['expiresAt'] >= time.time() + 5*60:
      return cached_tok['accessToken']
    # Grab the token.
    url = (
        'http://metadata.google.internal/computeMetadata/v1/instance'
        '/service-accounts/%s/token' % account)
    headers = {'Metadata-Flavor': 'Google'}
    try:
      resp = json.load(
          urllib2.urlopen(urllib2.Request(url, headers=headers), timeout=20))
    except IOError as e:
      logging.error('Failed to grab GCE access token: %s', e)
      raise
    tok = {
      'accessToken': resp['access_token'],
      'expiresAt': time.time() + resp['expires_in'],
    }
    _CACHED_OAUTH2_TOKEN[account] = tok
    return tok['accessToken']


def oauth2_available_scopes(account='default'):
  """Returns a list of OAuth2 scopes granted to GCE service account."""
  metadata = get_metadata()
  if not metadata:
    return []
  accounts = metadata['instance']['serviceAccounts']
  return accounts.get(account, {}).get('scopes') or []


@tools.cached
def get_zone():
  """Returns the zone containing the GCE VM."""
  # Format is projects/<id>/zones/<zone>
  metadata = get_metadata()
  return unicode(metadata['instance']['zone'].rsplit('/', 1)[-1])


@tools.cached
def get_machine_type():
  """Returns the GCE machine type."""
  # Format is projects/<id>/machineTypes/<machine_type>
  metadata = get_metadata()
  return unicode(metadata['instance']['machineType'].rsplit('/', 1)[-1])


@tools.cached
def get_tags():
  """Returns a list of instance tags or empty list if not GCE VM."""
  return get_metadata()['instance']['tags']


@tools.cached
def can_send_metric(service_account):
  """True if 'send_metric' really does something."""
  # Scope to use Cloud Monitoring.
  scope = 'https://www.googleapis.com/auth/monitoring'
  return scope in oauth2_available_scopes(account=service_account)
