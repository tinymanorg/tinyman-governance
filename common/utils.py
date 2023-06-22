from datetime import datetime
from pprint import pprint
from zoneinfo import ZoneInfo

from algosdk.encoding import encode_address, decode_address

from common.constants import DAY, WEEK
from locking.constants import ACCOUNT_POWER_SIZE, TOTAL_POWER_SIZE, TOTAL_POWERS, SLOPE_CHANGES, TOTAL_POWER_COUNT_KEY, TOTAL_POWER_BOX_ARRAY_LEN, ACCOUNT_POWER_BOX_ARRAY_LEN, TWO_TO_THE_64, MAX_LOCK_TIME
from proposal_voting.constants import PROPOSAL_BOX_PREFIX, ATTENDANCE_BOX_PREFIX
from rewards.constants import REWARD_HISTORY_SIZE, REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_HISTORY
from staking_voting.constants import VOTE_BOX_PREFIX


def itob(value, length=8):
    """ The same as teal itob - int to 8 bytes """
    return value.to_bytes(length, 'big')


def btoi(value):
    return int.from_bytes(value, 'big')


def sign_txns(txns, secret_key):
    return [txn.sign(secret_key) for txn in txns]


def parse_box_account_state(raw_box):
    data = dict(
        locked_amount=btoi(raw_box[:8]),
        lock_end_time=btoi(raw_box[8:16]),
        power_count=btoi(raw_box[16:24]),
    )
    data["lock_end_datetime"] = datetime.fromtimestamp(data["lock_end_time"], ZoneInfo("UTC"))
    return data


def parse_box_account_power(raw_box):
    box_size = ACCOUNT_POWER_SIZE
    rows = [raw_box[i:i + box_size] for i in range(0, len(raw_box), box_size)]
    powers = []
    for row in rows:
        if row == (b'\x00' * box_size):
            break

        powers.append(
            dict(
                bias=btoi(row[:8]),
                timestamp=btoi(row[8:16]),
                slope=btoi(row[16:32]),
                cumulative_power=btoi(row[32:48]),
                datetime=datetime.fromtimestamp(btoi(row[8:16]), ZoneInfo("UTC"))
            )
        )
    return powers


def parse_box_total_power(raw_box):
    box_size = TOTAL_POWER_SIZE
    rows = [raw_box[i:i + box_size] for i in range(0, len(raw_box), box_size)]
    powers = []
    for row in rows:
        if row == (b'\x00' * box_size):
            break

        powers.append(
            dict(
                bias=btoi(row[:8]),
                timestamp=btoi(row[8:16]),
                slope=btoi(row[16:32]),
                cumulative_power=btoi(row[32:48]),
            )
        )
    return powers


def parse_box_slope_change(raw_box):
    return dict(
        slope_delta=btoi(raw_box[:16]),
    )


def parse_box_staking_proposal(raw_box):
    data = dict(
        index=btoi(raw_box[:8]),
        creation_timestamp=btoi(raw_box[8:16]),
        voting_start_timestamp=btoi(raw_box[16:24]),
        voting_end_timestamp=btoi(raw_box[24:32]),
        voting_power=btoi(raw_box[32:40]),
        vote_count=btoi(raw_box[40:48]),
        is_cancelled=btoi(raw_box[48:49]),
    )
    return data


def parse_box_proposal(raw_box):
    data = dict(
        index=btoi(raw_box[:8]),
        creation_timestamp=btoi(raw_box[8:16]),
        voting_start_timestamp=btoi(raw_box[16:24]),
        voting_end_timestamp=btoi(raw_box[24:32]),
        snapshot_total_voting_power=btoi(raw_box[32:40]),
        vote_count=btoi(raw_box[40:48]),
        quorum_numerator=btoi(raw_box[48:56]),
        against_vote_amount=btoi(raw_box[56:64]),
        for_vote_amount=btoi(raw_box[64:72]),
        abstain_vote_amount=btoi(raw_box[72:80]),
        is_cancelled=btoi(raw_box[80:81]),
        is_executed=btoi(raw_box[81:82]),
        is_quorum_reached=btoi(raw_box[82:83]),
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
                timestamp=btoi(row[:8]),
                reward_amount=btoi(row[8:16]),
            )
        )
    return reward_histories


def print_boxes(boxes):
    for key, value in sorted(list(boxes.items()), key=lambda box: box[0]):
        if TOTAL_POWERS in key:
            index = btoi(key[len(TOTAL_POWERS):])
            print("TotalPower" + f"_{index}")
            powers = parse_box_total_power(value)
            for i, power in enumerate(powers):
                print("-", i, power)
        elif SLOPE_CHANGES in key:
            timestamp = btoi(key[len(SLOPE_CHANGES):])
            dt = datetime.fromtimestamp(timestamp, ZoneInfo("UTC"))
            print("SlopeChange" + f"_{btoi(key[len(SLOPE_CHANGES):])}")
            print("-", dt, parse_box_slope_change(value))
        elif key.startswith(PROPOSAL_BOX_PREFIX):
            if len(value) == 49:
                proposal_id = btoi(key[len(PROPOSAL_BOX_PREFIX):])
                print(f"PROPOSALS {proposal_id}")
                pprint(parse_box_staking_proposal(value))
            elif len(value) == 115:
                proposal_id = btoi(key[len(PROPOSAL_BOX_PREFIX):])
                print(f"PROPOSALS {proposal_id}")
                pprint(parse_box_proposal(value))
            else:
                raise NotImplementedError()
        elif key.startswith(VOTE_BOX_PREFIX) and len(key) == 17:
            proposal_id = btoi(key[1:9])
            asset_id = btoi(key[9:17])
            vote_amount = btoi(value)
            print(f"Proposal {proposal_id} - Asset ID {asset_id}", vote_amount)
        elif key.startswith(ATTENDANCE_BOX_PREFIX) and len(key) == 41:
            address = encode_address(key[1:33])
            box_index = btoi(key[33:])
            attendance_array = [v for v in value]
            print(f"ATTENDANCE {address}:{box_index}", attendance_array)
        elif len(value) == 1008:
            powers = parse_box_account_power(value)
            print(encode_address(key[:32]) + f"_{btoi(key[32:])}")
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


def get_latest_account_power_indexes(ledger, app_id, user_address):
    account_state = parse_box_account_state(ledger.boxes[app_id][decode_address(user_address)])
    power_count = account_state["power_count"]
    latest_index = power_count - 1

    box_index = latest_index // ACCOUNT_POWER_BOX_ARRAY_LEN
    array_index = latest_index % ACCOUNT_POWER_BOX_ARRAY_LEN
    is_full = not power_count % ACCOUNT_POWER_BOX_ARRAY_LEN

    return box_index, array_index, is_full


def get_account_power_index_at(ledger, app_id, user_address, timestamp):
    account_power_index = None
    if raw_account_state := ledger.boxes[app_id].get(decode_address(user_address)):
        account_state = parse_box_account_state(raw_account_state)
        power_count = account_state["power_count"]

        box_count = power_count // ACCOUNT_POWER_BOX_ARRAY_LEN
        box_count += bool(power_count % ACCOUNT_POWER_BOX_ARRAY_LEN)

        account_powers = []
        for box_index in range(box_count):
            raw_box = ledger.boxes[app_id][decode_address(user_address) + itob(box_index)]
            account_powers.extend(parse_box_account_power(raw_box))

        for index, account_power in enumerate(account_powers):
            if timestamp >= account_power["timestamp"]:
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
        raw_box = ledger.boxes[app_id][TOTAL_POWERS + itob(box_index)]
        total_powers.extend(parse_box_total_power(raw_box))

    for index, total_power in enumerate(total_powers):
        if timestamp >= total_power["timestamp"]:
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
        raw_box = ledger.boxes[app_id][REWARD_HISTORY + itob(box_index)]
        reward_histories.extend(parse_box_reward_history(raw_box))

    for index, reward_history in enumerate(reward_histories):
        if timestamp >= reward_history["timestamp"]:
            reward_history_index = index
        else:
            break

    return reward_history_index


def get_latest_checkpoint_timestamp(ledger, app_id):
    box_index, array_index, _ = get_latest_total_powers_indexes(ledger, app_id)
    boxes = ledger.boxes[app_id]
    timestamp = parse_box_total_power(boxes[TOTAL_POWERS + itob(box_index)])[array_index]["timestamp"]
    return timestamp


def get_slope(locked_amount):
    return locked_amount * TWO_TO_THE_64 // MAX_LOCK_TIME


def get_bias(slope, time_delta):
    assert time_delta >= 0
    return (slope * time_delta) // TWO_TO_THE_64


def get_voting_power(slope, remaining_time):
    return slope * remaining_time // 2 ** 64


def get_start_time_of_day(value):
    return (value // DAY) * DAY


def get_start_timestamp_of_week(value):
    return (value // WEEK) * WEEK


def get_required_minimum_balance_of_box(box_name, box_size):
    return 2_500 + 400 * (len(box_name) + box_size)
