#!/usr/bin/python3
#
# Copyright (c) 2019 Foundries.io
# SPDX-License-Identifier: Apache-2.0
#
import argparse
import hashlib
import json
import os
import sys

from copy import deepcopy
from datetime import datetime
from zipfile import ZipFile

import logging

import requests

logging.basicConfig(level='INFO')


class NullAuth(requests.auth.AuthBase):
    '''force requests to ignore the ``.netrc``

    JobServ will create a .netrc token for users needing to access the
    Run. This tells requests not to use that, and instead use the oauth
    token we create.
    '''

    def __call__(self, r):
        return r


def get_token(oauth_server, client, secret):
    data = {'grant_type': 'client_credentials'}
    url = oauth_server
    if url[-1] != '/':
        url += '/'
    url += 'token'
    r = requests.post(url, data=data, auth=(client, secret))
    r.raise_for_status()
    return r.json()['access_token']


def add_target(targets_json, app, version, content):
    with open(args.targets_json) as f:
        data = json.load(f)

    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    content = content.encode()
    m = hashlib.sha256()
    m.update(content)
    data['targets'][app + '-' + version] = {
        'hashes': {'sha256': m.hexdigest()},
        'length': len(content),
        'custom': {
            'targetFormat': 'BINARY',
            'version': version,
            'name': app,
            'hardwareIds': ['all'],
            'createdAt': now,
            'updatedAt': now,
        },
    }
    with open(args.targets_json, 'w') as f:
        json.dump(data, f, indent=2)


def publish(args):
    with ZipFile(args.credentials) as zf:
        with zf.open('tufrepo.url') as f:
            repourl = f.read().strip().decode()

        with zf.open('treehub.json') as f:
            oauth2 = json.load(f).get('oauth2')

    if repourl[-1] != '/':
        repourl += '/'

    token = ''
    if oauth2:
        token = get_token(
            oauth2['server'], oauth2['client_id'], oauth2['client_secret'])

    app = os.path.basename(args.dockerapp.name)
    url = repourl + 'api/v1/user_repo/targets/' + app + '-' + args.version
    r = requests.put(url,
                     params={'name': app, 'version': args.version},
                     files={'file': ('filename', args.dockerapp)},
                     headers={'Authorization': 'Bearer ' + token},
                     auth=NullAuth())
    if r.status_code in (200, 412):
        # Use our copy of targets.json that includes custom.docker_apps data
        # and not the one the server may have generated
        os.lseek(args.dockerapp.fileno(), 0, 0)
        add_target(args.targets_json, app, args.version, args.dockerapp.read())
    elif r.status_code != 200:
        sys.exit('Unable to create %s: HTTP_%d\n%s' % (
            url, r.status_code, r.text))


def add_build(args):
    latest = {}

    tags = os.environ.get('OTA_LITE_TAG')
    if tags:
        tags = [x.strip() for x in tags.split(',')]

    with open(args.targets_json) as f:
        data = json.load(f)
        for name, target in data['targets'].items():
            if target['custom']['targetFormat'] == 'OSTREE':
                if target['custom'].get('tags') == ['premerge']:
                    continue
                hwid = target['custom']['hardwareIds'][0]
                cur = latest.get(hwid)
                ver = int(target['custom']['version'])
                if not cur or int(cur['custom']['version']) < ver:
                    latest[hwid] = target
    logging.info('Latest targets: %r', latest)
    for target in latest.values():
        target = deepcopy(target)
        data['targets'][target['custom']['name'] + '-' + args.version] = target

        target['custom']['version'] = args.version
        if tags:
            target['custom']['tags'] = tags
        apps = {}
        target['custom']['docker_apps'] = apps
        for app in args.apps:
            filename = os.path.basename(app) + '-' + args.version
            name = os.path.splitext(filename)[0]
            apps[name] = {'filename': filename}
        logging.info('Targets with apps: %r', target)

    with open(args.targets_json, 'w') as f:
        json.dump(data, f, indent=2)


def get_args():
    parser = argparse.ArgumentParser(
        'Manage dockerapps on the OTA Connect reposerver')

    cmds = parser.add_subparsers(title='Commands')

    p = cmds.add_parser('publish')
    p.set_defaults(func=publish)
    p.add_argument('dockerapp', type=argparse.FileType('r'))
    p.add_argument('credentials', type=argparse.FileType('rb'))
    p.add_argument('version')
    p.add_argument('targets_json')

    p = cmds.add_parser('add-build')
    p.set_defaults(func=add_build)
    p.add_argument('credentials', type=argparse.FileType('rb'))
    p.add_argument('version')
    p.add_argument('targets_json')
    p.add_argument('apps', nargs='+')

    return parser.parse_args()


if __name__ == '__main__':
    args = get_args()
    if getattr(args, 'func', None):
        args.func(args)
