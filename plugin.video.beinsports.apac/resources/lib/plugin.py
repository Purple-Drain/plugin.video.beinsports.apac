import codecs
import json
import time
import re
from xml.sax.saxutils import escape

import arrow
from kodi_six import xbmc
from slyguy import plugin, gui, userdata, signals, inputstream

from .api import API
from .language import _
from .constants import WV_LICENSE_URL, HEADERS
from .settings import settings
from .menu_logic import (RECENT_TYPE, UPCOMING_TYPE, fixture_items,
                         group_fixtures, live_events, select_vod_rows,
                         variant_label, vod_items)

api = API()


@signals.on(signals.BEFORE_DISPATCH)
def before_dispatch():
    api.new_session()
    plugin.logged_in = api.logged_in


@plugin.route('')
def home(**kwargs):
    folder = plugin.Folder(cacheToDisc=False)

    if not api.logged_in:
        folder.add_item(label=_(_.LOGIN, _bold=True), path=plugin.url_for(login), bookmark=False)
    else:
        folder.add_item(label=_(_.LIVE_TV, _bold=True), path=plugin.url_for(live_tv))
        folder.add_item(label='Catch Up', path=plugin.url_for(catch_up))
        folder.add_item(label='Upcoming Live', path=plugin.url_for(upcoming_live))
        folder.add_item(label='Recently Live', path=plugin.url_for(recently_live))

        if settings.getBool('bookmarks', True):
            folder.add_item(label=_(_.BOOKMARKS, _bold=True), path=plugin.url_for(plugin.ROUTE_BOOKMARKS), bookmark=False)

        folder.add_item(label=_.LOGOUT, path=plugin.url_for(logout), _kiosk=False, bookmark=False)

    folder.add_item(label=_.SETTINGS, path=plugin.url_for(plugin.ROUTE_SETTINGS), _kiosk=False, bookmark=False)
    return folder


@plugin.route()
def login(**kwargs):
    options = [
        [_.DEVICE_CODE, _device_code],
        [_.EMAIL_PASSWORD, _email_password],
    ]

    index = gui.context_menu([x[0] for x in options])
    if index == -1 or not options[index][1]():
        return

    gui.refresh()


def _email_password():
    username = gui.input(_.ASK_EMAIL, default=userdata.get('username', '')).strip()
    if not username:
        return

    userdata.set('username', username)

    password = gui.input(_.ASK_PASSWORD, hide_input=True).strip()
    if not password:
        return

    api.login(username=username, password=password)
    return True


def _device_code():
    start = time.time()
    data = api.device_code()
    monitor = xbmc.Monitor()

    with gui.progress(_(_.DEVICE_LINK_STEPS, code=data['Code'].upper()), heading=_.DEVICE_CODE) as progress:
        while (time.time() - start) < data['ExpireDate']:
            for i in range(5):
                if progress.iscanceled() or monitor.waitForAbort(1):
                    return

                progress.update(int(((time.time() - start) / data['ExpireDate']) * 100))

            if api.device_login(data['Code']):
                return True


def _get_logo(url):
    return re.sub('_[0-9]+X[0-9]+.', '.', url)


@plugin.route()
def live_tv(**kwargs):
    folder = plugin.Folder(_.LIVE_TV)

    now = arrow.now()
    for row in sorted(api.live_channels(), key=lambda x: x['SortOrder']):
        plot = u''
        for epg in row['Program']:
            start = arrow.get(epg['StartTime'])
            stop = arrow.get(epg['EndTime'])

            if (now > start and now < stop) or start > now:
                plot += u'[{}] {}\n'.format(start.to('local').format('h:mma'), epg['Title'])

        folder.add_item(
            label = row['Name'],
            art = {'thumb': _get_logo(row['Logo']), 'fanart': row['Poster']},
            info = {'plot': plot},
            path = plugin.url_for(play, channel_id=row['Id'], _is_live=True),
            playable = True,
        )

    return folder


@plugin.route()
@plugin.login_required()
def play(channel_id=None, vod_id=None, **kwargs):
    asset = api.play(channel_id, vod_id)

    _headers = {}
    _headers.update(HEADERS)
    _headers.update({
        'Authorization': asset['DrmToken'],
        'X-CB-Ticket': asset['DrmTicket'],
        'X-ErDRM-Message': asset['DrmTicket'],
    })

    return plugin.Item(
        path = asset['Path'] + '?' + asset['CdnTicket'],
        inputstream = inputstream.Widevine(license_key=WV_LICENSE_URL),
        headers = _headers,
    )


def _add_playable(folder, item, label=None):
    folder.add_item(
        label=label or item.get('Title') or item.get('Name') or 'Untitled',
        info={'plot': item.get('Subtitle') or '',
              'duration': item.get('Duration') or 0},
        art={'thumb': item.get('Poster')},
        path=plugin.url_for(play, vod_id=item.get('Id')),
        playable=True,
    )


@plugin.route()
def catch_up(**kwargs):
    folder = plugin.Folder('Catch Up')
    for row in select_vod_rows(api._menu()):
        name = row.get('Name') or 'VOD'
        folder.add_item(label=name, path=plugin.url_for(catch_up_row, name=name))
    return folder


@plugin.route()
def catch_up_row(name=None, **kwargs):
    """One competition: a folder per fixture (full match + highlights
    grouped on beIN's shared content id, see menu_logic.group_fixtures),
    ungrouped items (round recaps) listed alongside, in beIN's own order."""
    folder = plugin.Folder(name or 'Catch Up')
    for kind, payload in group_fixtures(vod_items(api._menu(), name)):
        if kind == 'fixture':
            folder.add_item(
                label=payload['title'],
                art={'thumb': payload.get('poster')},
                path=plugin.url_for(catch_up_fixture, name=name, key=payload['key']),
            )
        else:
            _add_playable(folder, payload)
    return folder


@plugin.route()
def catch_up_fixture(name=None, key=None, **kwargs):
    """One fixture: its variants as playable items, full match first."""
    items = fixture_items(vod_items(api._menu(), name), key)
    folder = plugin.Folder(items[0].get('Title') if items else (name or 'Fixture'))
    for item in items:
        _add_playable(folder, item, label=variant_label(item))
    return folder


def _live_events_folder(title, kind):
    folder = plugin.Folder(title)
    for item in live_events(api._menu(), kind):
        plot = u'{}\n{} - {}'.format(
            item.get('Subtitle') or '',
            item.get('EventStartTime') or '', item.get('EventEndTime') or '')
        # Info-only: these items carry no ChannelId/VodId (see menu_logic's
        # live_events docstring), so no playable path is offered.
        folder.add_item(
            label=item.get('Title') or 'Event',
            info={'plot': plot},
            art={'thumb': item.get('Poster')},
        )
    return folder


@plugin.route()
def upcoming_live(**kwargs):
    return _live_events_folder('Upcoming Live', UPCOMING_TYPE)


@plugin.route()
def recently_live(**kwargs):
    return _live_events_folder('Recently Live', RECENT_TYPE)


@plugin.route()
def logout(**kwargs):
    if not gui.yes_no(_.LOGOUT_YES_NO):
        return

    api.logout()
    gui.refresh()


@plugin.route()
@plugin.merge()
@plugin.login_required()
def playlist(output, **kwargs):
    with codecs.open(output, 'w', encoding='utf8') as f:
        f.write(u'#EXTM3U')

        for row in api.live_channels():
            f.write(u'\n#EXTINF:-1 tvg-id="{id}" tvg-logo="{logo}",{name}\n{url}'.format(
                id=row['Id'], logo=_get_logo(row['Logo']), name=row['Name'],
                    url=plugin.url_for(play, channel_id=row['Id'], _is_live=True)))


@plugin.route()
@plugin.merge()
@plugin.login_required()
def epg(output, **kwargs):
    with codecs.open(output, 'w', encoding='utf8') as f:
        f.write(u'<?xml version="1.0" encoding="utf-8" ?><tv>')

        for row in api.epg(days=settings.getInt('epg_days', 3)):
            channel = row['Channel']

            f.write(u'<channel id="{}"><display-name>{}</display-name><icon src="{}"/></channel>'.format(
                channel['Id'], escape(channel['Name']), escape(_get_logo(channel['Logo']))))

            for program in row['EpgList']:
                f.write(u'<programme channel="{}" start="{}" stop="{}"><title>{}</title><sub-title>{}</sub-title><icon src="{}"/></programme>'.format(
                    channel['Id'], arrow.get(program['StartTime']).format('YYYYMMDDHHmmss Z'), arrow.get(program['EndTime']).format('YYYYMMDDHHmmss Z'),
                        escape(program['Title']), escape(program['Subtitle']), program['Poster']))

        f.write(u'</tv>')


@plugin.route()
@plugin.merge()
@plugin.login_required()
def menu_dump(output, **kwargs):
    """Write api._menu()'s raw response to `output` as JSON -- the same
    RunPlugin(...&output=<path>)-then-poll handshake this file's own epg()
    route already uses for IPTV Merge, reused here so kodi-strm-pipeline's
    service.sport.sync can read the full menu (Type 6/7 rows) for its own
    Football hub without reimplementing this add-on's session handling.
    Never shown in this add-on's own home menu -- triggered only by that
    other add-on's service."""
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(api._menu(), f)
