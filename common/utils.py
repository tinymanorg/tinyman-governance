from datetime import datetime
from pprint import pprint
from zoneinfo import ZoneInfo

from algosdk.encoding import encode_address, decode_address
from tinyman.governance.vault.constants import TOTAL_POWERS, SLOPE_CHANGES, TOTAL_POWER_COUNT_KEY, TOTAL_POWER_BOX_ARRAY_LEN, ACCOUNT_POWER_BOX_ARRAY_LEN
from tinyman.governance.vault.storage import parse_box_total_power, parse_box_slope_change, parse_box_account_power, parse_box_account_state
from tinyman.utils import bytes_to_int, int_to_bytes

from proposal_voting.constants import PROPOSAL_BOX_PREFIX, ATTENDANCE_BOX_PREFIX
from rewards.constants import REWARD_HISTORY_SIZE, REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_HISTORY_BOX_PREFIX
from staking_voting.constants import VOTE_BOX_PREFIX


def check_nth_bit_from_left(input_bytes, n):
    # ensure n is within the range of the bytes
    if n >= len(input_bytes) * 8:
        raise ValueError(f"n should be less than {len(input_bytes) * 8}")

    # convert bytes to int
    num = int.from_bytes(input_bytes, 'big')

    # calculate which bit to check from the left
    bit_to_check = (len(input_bytes) * 8 - 1) - n

    # create a number with nth bit set
    nth_bit = 1 << bit_to_check

    # if the nth bit is set in the given number, return 1. Otherwise, return 0
    if num & nth_bit:
        return 1
    else:
        return 0


def sign_txns(txns, secret_key):
    return [txn.sign(secret_key) for txn in txns]


def parse_box_staking_proposal(raw_box):
    data = dict(
        index=bytes_to_int(raw_box[:8]),
        creation_timestamp=bytes_to_int(raw_box[8:16]),
        voting_start_timestamp=bytes_to_int(raw_box[16:24]),
        voting_end_timestamp=bytes_to_int(raw_box[24:32]),
        voting_power=bytes_to_int(raw_box[32:40]),
        vote_count=bytes_to_int(raw_box[40:48]),
        is_cancelled=bytes_to_int(raw_box[48:49]),
    )
    return data


def parse_box_proposal(raw_box):
    data = dict(
        index=bytes_to_int(raw_box[:8]),
        creation_timestamp=bytes_to_int(raw_box[8:16]),
        voting_start_timestamp=bytes_to_int(raw_box[16:24]),
        voting_end_timestamp=bytes_to_int(raw_box[24:32]),
        snapshot_total_voting_power=bytes_to_int(raw_box[32:40]),
        vote_count=bytes_to_int(raw_box[40:48]),
        quorum_numerator=bytes_to_int(raw_box[48:56]),
        against_vote_amount=bytes_to_int(raw_box[56:64]),
        for_vote_amount=bytes_to_int(raw_box[64:72]),
        abstain_vote_amount=bytes_to_int(raw_box[72:80]),
        is_cancelled=bytes_to_int(raw_box[80:81]),
        is_executed=bytes_to_int(raw_box[81:82]),
        is_quorum_reached=bytes_to_int(raw_box[82:83]),
        proposer=encode_address(raw_box[83:115]),
    )
    return data


def parse_box_reward_history(raw_box):
    box_size = REWARD_HISTORY_SIZE
    rows = [raw_box[i:i + box_size] for i in range(0, len(raw_box), box_size)]
    reward_histories = []
    for row in rows:
        if row == (b'\x00' * box_size):
            break

        reward_histories.append(
            dict(
                timestamp=bytes_to_int(row[:8]),
                reward_amount=bytes_to_int(row[8:16]),
            )
        )
    return reward_histories


def print_boxes(boxes):
    for key, value in sorted(list(boxes.items()), key=lambda box: box[0]):
        if TOTAL_POWERS in key:
            index = bytes_to_int(key[len(TOTAL_POWERS):])
            print("TotalPower" + f"_{index}")
            powers = parse_box_total_power(value)
            for i, power in enumerate(powers):
                print("-", i, power)
        elif SLOPE_CHANGES in key:
            timestamp = bytes_to_int(key[len(SLOPE_CHANGES):])
            dt = datetime.fromtimestamp(timestamp, ZoneInfo("UTC"))
            print("SlopeChange" + f"_{bytes_to_int(key[len(SLOPE_CHANGES):])}")
            print("-", dt, parse_box_slope_change(value))
        elif key.startswith(PROPOSAL_BOX_PREFIX):
            if len(value) == 49:
                proposal_id = bytes_to_int(key[len(PROPOSAL_BOX_PREFIX):])
                print(f"PROPOSALS {proposal_id}")
                pprint(parse_box_staking_proposal(value))
            elif len(value) == 115:
                proposal_id = bytes_to_int(key[len(PROPOSAL_BOX_PREFIX):])
                print(f"PROPOSALS {proposal_id}")
                pprint(parse_box_proposal(value))
            else:
                raise NotImplementedError()
        elif key.startswith(VOTE_BOX_PREFIX) and len(key) == 17:
            proposal_id = bytes_to_int(key[1:9])
            asset_id = bytes_to_int(key[9:17])
            vote_amount = bytes_to_int(value)
            print(f"Proposal {proposal_id} - Asset ID {asset_id}", vote_amount)
        elif key.startswith(ATTENDANCE_BOX_PREFIX) and len(key) == 41:
            address = encode_address(key[1:33])
            box_index = bytes_to_int(key[33:])
            attendance_array = [check_nth_bit_from_left(value, i) for i in range(0, (len(value) * 8))]
            print(f"ATTENDANCE {address}:{box_index}", attendance_array)
        elif len(value) == 1008:
            powers = parse_box_account_power(value)
            print(encode_address(key[:32]) + f"_{bytes_to_int(key[32:])}")
            for i, power in enumerate(powers):
                print("-", i, power)
        elif len(value) == 24:
            print(encode_address(key))
            print(parse_box_account_state(value))


def get_latest_total_powers_indexes(ledger, app_id):
    total_power_count = ledger.global_states[app_id][TOTAL_POWER_COUNT_KEY]

    latest_index = total_power_count - 1
    box_index = latest_index // TOTAL_POWER_BOX_ARRAY_LEN
    array_index = latest_index % TOTAL_POWER_BOX_ARRAY_LEN
    is_full = not total_power_count % TOTAL_POWER_BOX_ARRAY_LEN

    return box_index, array_index, is_full


def get_account_power_index_at(ledger, app_id, user_address, timestamp):
    account_power_index = None
    if raw_account_state := ledger.boxes[app_id].get(decode_address(user_address)):
        account_state = parse_box_account_state(raw_account_state)
        power_count = account_state.power_count

        box_count = power_count // ACCOUNT_POWER_BOX_ARRAY_LEN
        box_count += bool(power_count % ACCOUNT_POWER_BOX_ARRAY_LEN)

        account_powers = []
        for box_index in range(box_count):
            raw_box = ledger.boxes[app_id][decode_address(user_address) + int_to_bytes(box_index)]
            account_powers.extend(parse_box_account_power(raw_box))

        for index, account_power in enumerate(account_powers):
            if timestamp >= account_power.timestamp:
                account_power_index = index
            else:
                break

    return account_power_index


def get_total_power_index_at(ledger, app_id, timestamp):
    total_power_index = None
    total_power_count = ledger.global_states[app_id][TOTAL_POWER_COUNT_KEY]

    box_count = total_power_count // TOTAL_POWER_BOX_ARRAY_LEN
    box_count += bool(total_power_count % TOTAL_POWER_BOX_ARRAY_LEN)

    total_powers = []
    for box_index in range(box_count):
        raw_box = ledger.boxes[app_id][TOTAL_POWERS + int_to_bytes(box_index)]
        total_powers.extend(parse_box_total_power(raw_box))

    for index, total_power in enumerate(total_powers):
        if timestamp >= total_power.timestamp:
            total_power_index = index
        else:
            break

    return total_power_index


def get_reward_history_index_at(ledger, app_id, timestamp):
    reward_history_index = None
    reward_history_count = ledger.global_states[app_id][b'reward_history_count']

    box_count = reward_history_count // REWARD_HISTORY_BOX_ARRAY_LEN
    box_count += bool(reward_history_count % REWARD_HISTORY_BOX_ARRAY_LEN)

    reward_histories = []
    for box_index in range(box_count):
        raw_box = ledger.boxes[app_id][REWARD_HISTORY_BOX_PREFIX + int_to_bytes(box_index)]
        reward_histories.extend(parse_box_reward_history(raw_box))

    for index, reward_history in enumerate(reward_histories):
        if timestamp >= reward_history["timestamp"]:
            reward_history_index = index
        else:
            break

    return reward_history_index


def get_required_minimum_balance_of_box(box_name, box_size):
    return 2_500 + 400 * (len(box_name) + box_size)
