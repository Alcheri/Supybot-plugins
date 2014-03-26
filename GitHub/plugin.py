###
# Copyright (c) 2011-2014, Valentin Lorentz
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import sys
import json
import time
import urllib
import socket
import threading
from string import Template
import supybot.log as log
import supybot.utils as utils
import supybot.world as world
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircmsgs as ircmsgs
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
import supybot.httpserver as httpserver

if sys.version_info[0] < 3:
    from cStringIO import StringIO
    quote_plus = urllib.quote_plus
else:
    from io import StringIO
    quote_plus = urllib.parse.quote_plus
try:
    from supybot.i18n import PluginInternationalization
    from supybot.i18n import internationalizeDocstring
    _ = PluginInternationalization('GitHub')
except:
    # This are useless functions that's allow to run the plugin on a bot
    # without the i18n plugin
    _ = lambda x:x
    internationalizeDocstring = lambda x:x

if sys.version_info[0] >= 3:
    def b(s):
        return s.encode('utf-8')
    def u(s):
        return s
    urlencode = urllib.parse.urlencode
else:
    def u(s):
        return s.decode('utf-8')
    def b(s):
        return s
    urlencode = urllib.urlencode

def flatten_subdicts(dicts, flat=None):
    """Change dict of dicts into a dict of strings/integers. Useful for
    using in string formatting."""
    if flat is None:
        # Instanciate the dictionnary when the function is run and now when it
        # is declared; otherwise the same dictionnary instance will be kept and
        # it will have side effects (memory exhaustion, ...)
        flat = {}
    if isinstance(dicts, list):
        return flatten_subdicts(dict(enumerate(dicts)))
    elif isinstance(dicts, dict):
        for key, value in dicts.items():
            if isinstance(value, dict):
                value = dict(flatten_subdicts(value))
                for subkey, subvalue in value.items():
                    flat['%s__%s' % (key, subkey)] = subvalue
            else:
                flat[key] = value
        return flat
    else:
        return dicts

#####################
# Server stuff
#####################

class GithubCallback(httpserver.SupyHTTPServerCallback):
    name = "GitHub announce callback"
    defaultResponse = _("""
    You shouldn't be here, this subfolder is not for you. Go back to the
    index and try out other plugins (if any).""")
    def doPost(self, handler, path, form):
        if not handler.address_string().endswith('.rs.github.com') and \
                not handler.address_string().endswith('.cloud-ips.com') and \
                not handler.address_string() == 'localhost' and \
                not handler.address_string().startswith('127.0.0.') and \
                not handler.address_string().startswith('192.30.252.') and \
                not handler.address_string().startswith('204.232.175.'):
            log.warning("""'%s' tried to act as a web hook for Github,
            but is not GitHub.""" % handler.address_string())
            self.send_response(403)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b('Error: you are not a GitHub server.'))
        else:
            headers = dict(self.headers)
            try:
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b('Thanks.'))
            except socket.error:
                pass
            self.plugin.announce.onPayload(headers, json.loads(form['payload'].value))

#####################
# API access stuff
#####################

def query(caller, type_, uri_end, args):
    args = dict([(x,y) for x,y in args.items() if y is not None])
    url = '%s/%s/%s?%s' % (caller._url(), type_, uri_end,
                           urlencode(args))
    if sys.version_info[0] >= 3:
        return json.loads(utils.web.getUrl(url).decode('utf8'))
    else:
        return json.load(utils.web.getUrlFd(url))

#####################
# Plugin itself
#####################

instance = None

@internationalizeDocstring
class GitHub(callbacks.Plugin):
    """Add the help for "@plugin help GitHub" here
    This should describe *how* to use this plugin."""

    def __init__(self, irc):
        global instance
        self.__parent = super(GitHub, self)
        callbacks.Plugin.__init__(self, irc)
        instance = self

        callback = GithubCallback()
        callback.plugin = self
        httpserver.hook('github', callback)
        for cb in self.cbs:
            cb.plugin = self



    class announce(callbacks.Commands):
        def _shorten_url(self, url):
            try:
                data = urlencode({'url': url})
                if sys.version_info[0] >= 3:
                    data = data.encode()
                    f = utils.web.getUrlFd('http://git.io/', data=data)
                    url = list(filter(lambda x:x[0] == 'Location',
                        f.headers._headers))[0][1].strip()
                else:
                    f = utils.web.getUrlFd('http://git.io/', data=data)
                    url = filter(lambda x:x.startswith('Location: '),
                            f.headers.headers)[0].split(': ', 1)[1].strip()
            except Exception as e:
                log.error('Cannot connect to git.io: %s' % e)
                return None
            return url
        def _createPrivmsg(self, irc, channel, payload, event, hidden=None):
            bold = ircutils.bold


            format_ = self.plugin.registryValue('format.%s' % event, channel)
            if not format_.strip():
                return
            repl = flatten_subdicts(payload)
            try_gitio = True
            for (key, value) in dict(repl).items():
                if key.endswith('url'):
                    if try_gitio:
                        url = self._shorten_url(value)
                    else:
                        url = None
                    if url:
                        repl[key + '__tiny'] = url
                    else:
                        repl[key + '__tiny'] = value
                        try_gitio = False
                elif key.endswith('ref'):
                    try:
                        repl[key + '__branch'] = value.split('/', 2)[2]
                    except IndexError:
                        pass
                elif isinstance(value, str):
                    repl[key + '__firstline'] = value.split('\n', 1)[0]
            repl.update({'__hidden': hidden or 0})
            command = Template(format_).safe_substitute(repl)
            #if hidden is not None:
            #    s += _(' (+ %i hidden commits)') % hidden
            #if sys.version_info[0] < 3:
            #        s = s.encode('utf-8')
            tokens = callbacks.tokenize(command)
            if not tokens:
                return
            fake_msg = ircmsgs.IrcMsg(command='PRIVMSG',
                    args=(channel, 'GITHUB'))
            try:
                self.plugin.Proxy(irc, fake_msg, tokens)
            except Exception as  e:
                self.plugin.log.exception('Error occured while running triggered command:')

        def onPayload(self, headers, payload):
            if 'full_name' in payload['repository']:
                repo = payload['repository']['full_name']
            elif 'name' in payload['repository']['owner']:
                repo = '%s/%s' % (payload['repository']['owner']['name'],
                                  payload['repository']['name'])
            else:
                repo = '%s/%s' % (payload['repository']['owner']['login'],
                                  payload['repository']['name'])
            event = headers['X-GitHub-Event']
            announces = self._load()
            if repo not in announces:
                log.info('Commit for repo %s not announced anywhere' % repo)
                return
            for channel in announces[repo]:
                for irc in world.ircs:
                    if channel in irc.state.channels:
                        break
                if event == 'push':
                    commits = payload['commits']
                    if channel not in irc.state.channels:
                        log.info('Cannot announce commit for repo %s on %s' %
                                 (repo, channel))
                    elif len(commits) == 0:
                        log.warning('GitHub push hook called without any commit.')
                    else:
                        hidden = None
                        last_commit = commits[-1]
                        if last_commit['message'].startswith('Merge ') and \
                                len(commits) > 5:
                            hidden = len(commits) + 1
                            commits = [last_commit]
                        payload2 = dict(payload)
                        for commit in commits:
                            payload2['__commit'] = commit
                            self._createPrivmsg(irc, channel, payload2,
                                    'push', hidden)
                else:
                    self._createPrivmsg(irc, channel, payload, event)

        def _load(self):
            announces = instance.registryValue('announces').split(' || ')
            if announces == ['']:
                return {}
            announces = [x.split(' | ') for x in announces]
            output = {}
            for repo, chan in announces:
                if repo not in output:
                    output[repo] = []
                output[repo].append(chan)
            return output

        def _save(self, data):
            list_ = []
            for repo, chans in data.items():
                list_.extend([' | '.join([repo,chan]) for chan in chans])
            string = ' || '.join(list_)
            instance.setRegistryValue('announces', value=string)

        @internationalizeDocstring
        def add(self, irc, msg, args, channel, owner, name):
            """[<channel>] <owner> <name>

            Announce the commits of the GitHub repository called
            <owner>/<name> in the <channel>.
            <channel> defaults to the current channel."""
            repo = '%s/%s' % (owner, name)
            announces = self._load()
            if repo not in announces:
                announces[repo] = [channel]
            elif channel in announces[repo]:
                irc.error(_('This repository is already announced to this '
                            'channel.'))
                return
            else:
                announces[repo].append(channel)
            self._save(announces)
            irc.replySuccess()
        add = wrap(add, ['channel', 'something', 'something'])

        @internationalizeDocstring
        def remove(self, irc, msg, args, channel, owner, name):
            """[<channel>] <owner> <name>

            Don't announce the commits of the GitHub repository called
            <owner>/<name> in the <channel> anymore.
            <channel> defaults to the current channel."""
            repo = '%s/%s' % (owner, name)
            announces = self._load()
            if repo not in announces:
                announces[repo] = []
            elif channel not in announces[repo]:
                irc.error(_('This repository is not yet announced to this '
                            'channel.'))
                return
            else:
                announces[repo].remove(channel)
            self._save(announces)
            irc.replySuccess()
        remove = wrap(remove, ['channel', 'something', 'something'])



    class repo(callbacks.Commands):
        def _url(self):
            url = instance.registryValue('api.url')
            if url == 'http://github.com/api/v2/json': # old api
                url = 'https://api.github.com'
                instance.setRegistryValue('api.url', value=url)
            return url

        @internationalizeDocstring
        def search(self, irc, msg, args, search, optlist):
            """<searched string> [--page <id>] [--language <language>]

            Searches the string in the repository names database. You can
            specify the page <id> of the results, and restrict the search
            to a particular programming <language>."""
            args = {'page': None, 'language': None}
            for name, value in optlist:
                if name in args:
                    args[name] = value
            results = query(self, 'legacy/repos/search',
                    quote_plus(search), args)
            reply = ' & '.join('%s/%s' % (x['owner'], x['name'])
                               for x in results['repositories'])
            if reply == '':
                irc.error(_('No repositories matches your search.'))
            else:
                irc.reply(u(reply))
        search = wrap(search, ['something',
                               getopts({'page': 'id',
                                        'language': 'somethingWithoutSpaces'})])
        @internationalizeDocstring
        def info(self, irc, msg, args, owner, name, optlist):
            """<owner> <repository> [--enable <feature> <feature> ...] \
            [--disable <feature> <feature>]

            Displays informations about <owner>'s <repository>.
            Enable or disable features (ie. displayed data) according to the
            request)."""
            enabled = ['watchers', 'forks', 'pushed_at', 'open_issues',
                       'description']
            for mode, features in optlist:
                features = features.split(' ')
                for feature in features:
                    if mode == 'enable':
                        enabled.append(feature)
                    else:
                        try:
                            enabled.remove(feature)
                        except ValueError:
                            # No error is raised, because:
                            # 1. it wouldn't break anything
                            # 2. it enhances cross-compatiblity
                            pass
            results = query(self, 'repos', '%s/%s' % (owner, name), {})
            output = []
            for key, value in results.items():
                if key in enabled:
                    output.append('%s: %s' % (key, value))
            irc.reply(u(', '.join(output)))
        info = wrap(info, ['something', 'something',
                           getopts({'enable': 'anything',
                                    'disable': 'anything'})])
    def die(self):
        self.__parent.die()
        httpserver.unhook('github')


Class = GitHub


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
