import argparse
import json
import os
import shutil
from datetime import datetime
from time import sleep
from typing import List

from pick import pick
from slacker import Slacker, Conversations


# channelId is the id of the channel/group/im you want to download history for.
def get_history(conversation: Conversations, channel_id: str, oldest: int = 0, limit: int = 100):
    messages = list()
    last_timestamp = None

    while True:
        response = conversation.history(channel=channel_id, latest=last_timestamp, oldest=oldest, limit=limit).body
        messages.extend(response['messages'])

        if response['has_more']:
            last_timestamp = messages[-1]['ts']  # -1 means last element in a list
            sleep(1)  # Respect the Slack API rate limit
        else:
            break

    messages.sort(key=lambda message: message['ts'])

    return messages


def mkdir(directory):
    if not os.path.isdir(directory):
        os.makedirs(directory)


# create datetime object from slack timestamp ('ts') string
def parse_time_stamp(time_stamp: str):
    if '.' in time_stamp:
        t_list = time_stamp.split('.')
        if len(t_list) != 2:
            raise ValueError('Invalid time stamp')
        else:
            return datetime.utcfromtimestamp(float(t_list[0]))


# move channel files from old directory to one with new channel name
def channel_rename(old_channel_name: str, new_channel_name: str):
    # check if any files need to be moved
    if not os.path.isdir(old_channel_name):
        return
    mkdir(new_channel_name)
    for file_name in os.listdir(old_channel_name):
        shutil.move(os.path.join(old_channel_name, file_name), new_channel_name)
    os.rmdir(old_channel_name)


def write_message_file(file_name: str, messages):
    directory = os.path.dirname(file_name)

    # if there's no data to write to the file, return
    if not messages:
        return

    if not os.path.isdir(directory):
        mkdir(directory)

    with open(file_name, 'w') as outFile:
        json.dump(messages, outFile, indent=4)


# parse messages by date
def parse_messages(room_dir, messages, room_type: str):
    name_change_flag = f'{room_type}_name'

    current_file_date = ''
    current_messages = list()
    for message in messages:
        # first store the date of the next message
        ts = parse_time_stamp(message['ts'])
        file_date = f'{ts:%Y-%m-%d}'

        # if it's on a different day, write out the previous day's messages
        if file_date != current_file_date:
            out_file_name = f'{room_dir}/{current_file_date}.json'
            write_message_file(out_file_name, current_messages)
            current_file_date = file_date
            current_messages = list()

        # check if current message is a name change
        # dms won't have name change events
        if room_type != "im" and ('subtype' in message) and message['subtype'] == name_change_flag:
            room_dir = message['name']
            old_room_path = message['old_name']
            new_room_path = room_dir
            channel_rename(old_room_path, new_room_path)

        current_messages.append(message)
    out_file_name = f'{room_dir}/{current_file_date}.json'
    write_message_file(out_file_name, current_messages)


def filter_conversations_by_name(channels_or_groups, channel_or_group_names):
    return [conversation for conversation in channels_or_groups if conversation['name'] in channel_or_group_names]


def prompt_for_public_channels(channels: List[dict]):
    channel_names = [channel['name'] for channel in channels]
    selected_channels = pick(channel_names, 'Select the Public Channels you want to export:', multi_select=True)
    return [channels[index] for _, index in selected_channels]


# fetch and write history for all public channels
def fetch_public_channels(channels: List[dict]):
    if dry_run:
        print("Public Channels selected for export:")
        for channel in channels:
            print(channel['name'])
        print()
        return

    for channel in channels:
        print(f"Fetching history for Public Channel: {channel['name']}")
        mkdir(channel['name'])
        messages = get_history(slack.conversations, channel['id'])
        parse_messages(channel['name'], messages, 'channel')


# write channels.json file
def dump_channel_file():
    print("Making channels file")

    private = list()
    mpim = list()

    for group in groups:
        if group['is_mpim']:
            mpim.append(group)
            continue
        private.append(group)

    # slack viewer wants DMs to have a members list, not sure why but doing as they expect
    for dm in dms:
        dm['members'] = [dm['user'], token_owner_id]

    # We will be overwriting this file on each run.
    with open('channels.json', 'w') as outFile:
        json.dump(channels, outFile, indent=4)
    with open('groups.json', 'w') as outFile:
        json.dump(private, outFile, indent=4)
    with open('mpims.json', 'w') as outFile:
        json.dump(mpim, outFile, indent=4)
    with open('dms.json', 'w') as outFile:
        json.dump(dms, outFile, indent=4)


def filter_direct_messages_by_user_name_or_id(dms, user_names_or_ids):
    user_ids = [user_ids_by_name.get(userNameOrId, userNameOrId) for userNameOrId in user_names_or_ids]
    return [dm for dm in dms if dm['user'] in user_ids]


def prompt_for_direct_messages(dms):
    dm_names = [user_names_by_id.get(dm['user'], f"{dm['user']} (name unknown)") for dm in dms]
    selected_dms = pick(dm_names, 'Select the 1:1 DMs you want to export:', multi_select=True)
    return [dms[index] for _, index in selected_dms]


# fetch and write history for all direct message conversations
# also known as IMs in the slack API.
def fetch_direct_messages(dms):
    if dry_run:
        print("1:1 DMs selected for export:")
        for dm in dms:
            print(user_names_by_id.get(dm['user'], dm['user'] + " (name unknown)"))
        print()
        return

    for dm in dms:
        name = user_names_by_id.get(dm['user'], dm['user'] + " (name unknown)")
        print(f"Fetching 1:1 DMs with {name}")
        dm_id = dm['id']
        mkdir(dm_id)
        messages = get_history(slack.conversations, dm['id'])
        parse_messages(dm_id, messages, "im")


def promptForGroups(groups):
    group_names = [group['name'] for group in groups]
    selected_groups = pick(group_names, 'Select the Private Channels and Group DMs you want to export:',
                           multi_select=True)
    return [groups[index] for _, index in selected_groups]


# fetch and write history for specific private channel
# also known as groups in the slack API.
def fetch_groups(groups):
    if dry_run:
        print("Private Channels and Group DMs selected for export:")
        for group in groups:
            print(group['name'])
        print()
        return

    for group in groups:
        group_dir = group['name']
        mkdir(group_dir)
        messages = list()
        print(f"Fetching history for Private Channel / Group DM: {group['name']}")
        messages = get_history(slack.conversations, group['id'])
        parse_messages(group_dir, messages, 'group')


# fetch all users for the channel and return a map userId -> userName
def get_user_map():
    global user_names_by_id, user_ids_by_name
    for user in users:
        user_names_by_id[user['id']] = user['name']
        user_ids_by_name[user['name']] = user['id']


# stores json of user info
def dump_user_file():
    # write to user file, any existing file needs to be overwritten.
    with open("users.json", 'w') as userFile:
        json.dump(users, userFile, indent=4)


# get basic info about the slack channel to ensure the authentication token works
def do_test_auth():
    test_auth = slack.auth.test().body
    team_name = test_auth['team']
    current_user = test_auth['user']
    print(f'Successfully authenticated for team {team_name} and user {current_user}')
    return test_auth


# Since Slacker does not Cache.. populate some reused lists
def bootstrap_key_values():
    global users, channels, groups, dms
    users = slack.users.list().body['members']
    print(f'Found {len(users)} Users')
    sleep(1)

    channels = slack.conversations.list(types='public_channel').body['channels']
    print(f'Found {len(channels)} Public Channels')
    sleep(1)

    groups = slack.conversations.list(types='private_channel, mpim').body['channels']
    print(f'Found {len(groups)} Private Channels or Group DMs')
    sleep(1)

    dms = slack.conversations.list(types='im').body['channels']
    print(f'Found {len(dms)} 1:1 DM conversations\n')
    sleep(1)

    get_user_map()


# Returns the conversations to download based on the command-line arguments
def select_conversations(all_conversations, command_line_arg, filter_function, prompt_function):
    global args
    if isinstance(command_line_arg, list) and len(command_line_arg) > 0:
        return filter_function(all_conversations, command_line_arg)
    elif command_line_arg is not None or not any_conversations_specified():
        if args.prompt:
            return prompt_function(all_conversations)
        else:
            return all_conversations
    else:
        return list()


# Returns true if any conversations were specified on the command line
def any_conversations_specified():
    global args
    return args.publicChannels is not None or args.groups is not None or args.directMessages is not None


# This method is used in order to create a empty Channel if you do not export public channels
# otherwise, the viewer will error and not show the root screen. Rather than forking the editor, I work with it.
def dump_dummy_channel():
    channel_name = channels[0]['name']
    mkdir(channel_name)
    file_date = f'{datetime.today():%Y-%m-%d}'
    out_file_name = f'{channel_name}/{file_date}.json'
    write_message_file(out_file_name, list())


def finalize():
    os.chdir('..')
    if zip_name:
        shutil.make_archive(zip_name, 'zip', output_directory, None)
        shutil.rmtree(output_directory)
    exit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Export Slack history')

    parser.add_argument('--token', required=True, help="Slack API token")
    parser.add_argument('--zip', help="Name of a zip file to output as")

    parser.add_argument(
        '--dryRun',
        action='store_true',
        default=False,
        help="List the conversations that will be exported (don't fetch/write history)")

    parser.add_argument(
        '--publicChannels',
        nargs='*',
        default=None,
        metavar='CHANNEL_NAME',
        help="Export the given Public Channels")

    parser.add_argument(
        '--groups',
        nargs='*',
        default=None,
        metavar='GROUP_NAME',
        help="Export the given Private Channels / Group DMs")

    parser.add_argument(
        '--directMessages',
        nargs='*',
        default=None,
        metavar='USER_NAME',
        help="Export 1:1 DMs with the given users")

    parser.add_argument(
        '--prompt',
        action='store_true',
        default=False,
        help="Prompt you to select the conversations to export")

    args = parser.parse_args()

    users = list()
    channels = list()
    groups = list()
    dms = list()
    user_names_by_id = dict()
    user_ids_by_name = dict()

    slack = Slacker(args.token)
    token_owner_id = do_test_auth()['user_id']

    bootstrap_key_values()

    dry_run = args.dryRun
    zip_name = args.zip

    output_directory = f"{datetime.today().strftime('%Y%m%d-%H%M%S')}-slack_export"
    mkdir(output_directory)
    os.chdir(output_directory)

    if not dry_run:
        dump_user_file()
        dump_channel_file()

    selected_channels = select_conversations(
        channels,
        args.publicChannels,
        filter_conversations_by_name,
        prompt_for_public_channels)

    selected_groups = select_conversations(
        groups,
        args.groups,
        filter_conversations_by_name,
        promptForGroups)

    selected_dms = select_conversations(
        dms,
        args.directMessages,
        filter_direct_messages_by_user_name_or_id,
        prompt_for_direct_messages)

    if len(selected_channels) > 0:
        fetch_public_channels(selected_channels)

    if len(selected_groups) > 0:
        if len(selected_channels) == 0:
            dump_dummy_channel()
        fetch_groups(selected_groups)

    if len(selected_dms) > 0:
        fetch_direct_messages(selected_dms)

    finalize()
