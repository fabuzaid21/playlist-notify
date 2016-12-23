#!/usr/bin/env python

import re
import cPickle as pickle
from os import path, getenv
import spotipy
import util
import argparse
from time import sleep
from twilio.rest import TwilioRestClient

# Twilio API
TWILIO_ACCOUNT_SID = getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN  = getenv('TWILIO_AUTH_TOKEN')
TWILIO_NUMBER      = getenv('TWILIO_NUMBER')

# Spotify API
SPOTIFY_USERNAME      = getenv('SPOTIFY_USERNAME')
SPOTIFY_CLIENT_ID     = getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI  = getenv('SPOTIFY_REDIRECT_URI')
SPOTIFY_API_SCOPE='''
playlist-read-collaborative
'''

PLAYLISTS = ['Firas & Samra: Day', 'Firas & Samra: Night']


PREV_PLAYLISTS_INFO_FILE='.playlist-notify'

# Return all tracks and the snapshot id for a given playlist id. The tracks are
# returned as a list of 2-tuples:
# (track id, 'added_by', username who added track)
def get_playlist_tracks_and_snapshot(spotify_api, user_id, playlist_id):
    playlist_info = spotify_api.user_playlist(user_id, playlist_id)
    return ([(t['track']['id'], t['added_by']['id']) for t in
        playlist_info['tracks']['items']], playlist_info['snapshot_id'])

# Assumes PREV_PLAYLISTS_INFO_FILE doesn't exist; searches through all of
# user's playlists to find the playlist id, snapshot id, and URL of the
# playlists we care about
def initialize_playlists_info(spotify_api):
    offset = 0
    playlists_info = {}
    while True:
        user_playlists = spotify_api.user_playlists(SPOTIFY_USERNAME, offset=offset)
        for playlist in user_playlists['items']:
            if playlist['name'] in PLAYLISTS:
                playlists_info[playlist['name']] = {
                        'id': playlist['id'],
                        'owner': playlist['owner']['id'],
                        'snapshot_id': playlist['snapshot_id'],
                        'tracks': get_playlist_tracks_and_snapshot(spotify_api,
                            playlist['owner']['id'], playlist['id'])[0],
                        'url': playlist['external_urls']['spotify']
                        }
                if len(playlists_info) == len(PLAYLISTS):
                    break
        if user_playlists['next']:
            offset = int(re.search(r'offset=(\d+)', user_playlists['next']).group(1))
        else:
            break
    return playlists_info

# Assumes that PREV_PLAYLISTS_INFO_FILE exists
def get_saved_playlists_info():
    with open(PREV_PLAYLISTS_INFO_FILE) as f:
        playlists_info = pickle.load(f)
        return playlists_info

def check_phone_number(phone_number):
    if not re.match(r'\+1\d{10}', phone_number):
        raise argparse.ArgumentTypeError('%s is not a valid U.S. phone number' % phone_number)
    return phone_number

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='''
    Sign up for text notifications for all your Spotify collaborative playlists.
    ''')
    parser.add_argument('-u', '--username', metavar='username', dest='username', required=True, help='Spotify username')
    parser.add_argument('-p', '--phone-number', metavar='phone_number (+12345678901)', dest='phone_number', required=True,
            type=check_phone_number, help='phone number to receive text messages')

    args = parser.parse_args()
    TARGET_PHONE_NUMBER = args.phone_number
    TARGET_USERNAME = args.username

    sp_oauth = util.prompt_for_user_authentication(SPOTIFY_USERNAME,
            scope=SPOTIFY_API_SCOPE, client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI)
    while True:

        spotify_token = sp_oauth.get_cached_token()['access_token']
        if not spotify_token:
            print 'Unable to acquire Spotify token; restart script'
            break

        print '''Polling for updates for %s; any changes to the following playlists:\n\n%s\n
will be alerted via text message to the following number:\n%s
        ''' % (TARGET_USERNAME, '\n'.join(PLAYLISTS), TARGET_PHONE_NUMBER)
        spotify_api = spotipy.Spotify(auth=spotify_token)

        if not path.isfile(PREV_PLAYLISTS_INFO_FILE):
            print '%s doesn\'t exist; initializing from scratch' % PREV_PLAYLISTS_INFO_FILE
            curr_playlists_info = initialize_playlists_info(spotify_api)
            with open(PREV_PLAYLISTS_INFO_FILE, 'w') as f:
                pickle.dump(curr_playlists_info, f)

        else:
            curr_playlists_info = get_saved_playlists_info()
            twilio_api = TwilioRestClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            for playlist_name, info in curr_playlists_info.iteritems():
                print 'Checking for any updates to "%s"' % playlist_name
                tracks, snapshot_id = get_playlist_tracks_and_snapshot(spotify_api, \
                        info['owner'], info['id'])
                if snapshot_id == info['snapshot_id']:
                    print 'No updates found for "%s"\n' % playlist_name
                    continue

                curr_playlists_info[playlist_name]['snapshot_id'] = snapshot_id
                if len(tracks) == len(info['tracks']):
                    print '"%s" was re-ordered; no new songs added\n' % playlist_name
                    curr_playlists_info[playlist_name]['tracks'] = tracks
                    continue

                # For now, assume new tracks are added at the end
                num_new_tracks = len(tracks) - len(info['tracks'])
                send_update = False
                for track in tracks[-num_new_tracks:]:
                    if track[1] != TARGET_USERNAME:
                        send_update = True
                        break

                curr_playlists_info[playlist_name]['tracks'] = tracks

                if send_update:
                    message_body = '%s added new songs to "%s"; have a listen: %s' % \
                        (SPOTIFY_USERNAME, playlist_name, info['url'])
                    print 'Sending \'%s\' to %s\n' % (message_body, TARGET_PHONE_NUMBER)
                    message = twilio_api.messages.create(body=message_body,
                        to=TARGET_PHONE_NUMBER,
                        from_=TWILIO_NUMBER)

            with open(PREV_PLAYLISTS_INFO_FILE, 'w') as f:
                pickle.dump(curr_playlists_info, f)

        print 'Checking again in 1 minute\n'
        sleep(60) # sleep for 60 seconds then try again

