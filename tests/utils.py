from datetime import datetime
from zoneinfo import ZoneInfo

from algosdk.encoding import encode_address, decode_address

from tests.constants import TOTAL_POWERS, SLOPE_CHANGES, MAX_LOCK_TIME, DAY, WEEK, PROPOSALS, MAX_OPTION_COUNT, TOTAL_POWER_BOX_ARRAY_LEN, TWO_TO_THE_64, ACCOUNT_POWER_BOX_ARRAY_LEN, ACCOUNT_POWER_SIZE, TOTAL_POWER_SIZE, REWARD_HISTORY_SIZE, REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_HISTORY


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
    n = ACCOUNT_POWER_SIZE
    rows = [raw_box[i:i+n] for i in range(0, len(raw_box), n)]
    powers = []
    for row in rows:
        if row == (b'\x00' * n):
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
    # data = dict(
    #     bias=btoi(raw_box[:8]),
    #     timestamp=btoi(raw_box[8:16]),
    #     slope=btoi(raw_box[16:32]),
    # )
    # data["datetime"] = datetime.fromtimestamp(data["timestamp"], ZoneInfo("UTC"))
    # return data


def parse_box_total_power(raw_box):
    n = TOTAL_POWER_SIZE
    rows = [raw_box[i:i+n] for i in range(0, len(raw_box), n)]
    powers = []
    for row in rows:
        if row == (b'\x00' * n):
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


def parse_box_proposal(raw_box):
    data = dict(
        creation_time=btoi(raw_box[:8]),
        voting_start_time=btoi(raw_box[8:16]),
        voting_end_time=btoi(raw_box[16:24]),
        option_count=btoi(raw_box[24:32]),
        vote_count=btoi(raw_box[32:40]),
        is_cancelled=btoi(raw_box[40:48]),
        is_executed=btoi(raw_box[48:56]),
        proposer=encode_address(raw_box[56:88]),
        votes=raw_box[88:216]
    )
    data["creation_date"] = datetime.fromtimestamp(data["creation_time"], ZoneInfo("UTC")).date().isoformat()
    data["voting_start_date"] = datetime.fromtimestamp(data["voting_start_time"], ZoneInfo("UTC")).date().isoformat()
    data["voting_end_date"] = datetime.fromtimestamp(data["voting_end_time"], ZoneInfo("UTC")).date().isoformat()
    data[f"vote_counts"] = [btoi(data["votes"][i * 8: (i + 1) * 8]) for i in range(MAX_OPTION_COUNT)]
    return data

def parse_box_reward_history(raw_box):
    n = REWARD_HISTORY_SIZE
    rows = [raw_box[i:i+n] for i in range(0, len(raw_box), n)]
    reward_histories = []
    for row in rows:
        if row == (b'\x00' * n):
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
        elif len(value) == 1008:
            powers = parse_box_account_power(value)
            print(encode_address(key[:32]) + f"_{btoi(key[32:])}")
            for i, power in enumerate(powers):
                print("-", i, power)
        elif len(value) == 24:
            print(encode_address(key))
            print(parse_box_account_state(value))
        elif PROPOSALS in key:
            proposal_id = btoi(key[len(PROPOSALS):])
            print(f"PROPOSALS {proposal_id}", parse_box_proposal(value))

# def parse_global_state():
#     {
#         b'total_power_count': 4,
#         b'tiny_asset_id': self.tiny_asset_id,
#         b'total_locked_amount': amount
#     }


def get_latest_checkpoint_indexes(ledger, app_id):
    total_power_count = ledger.global_states[app_id][b'total_power_count']

    latest_index = total_power_count - 1
    box_index = latest_index // TOTAL_POWER_BOX_ARRAY_LEN
    array_index = latest_index % TOTAL_POWER_BOX_ARRAY_LEN

    return box_index, array_index

def get_latest_account_power_indexes(ledger, app_id, user_address):
    account_state = parse_box_account_state(ledger.boxes[app_id][decode_address(user_address)])
    power_count = account_state["power_count"]
    latest_index = power_count - 1

    box_index = latest_index // ACCOUNT_POWER_BOX_ARRAY_LEN
    array_index = latest_index % ACCOUNT_POWER_BOX_ARRAY_LEN

    return box_index, array_index

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
    total_power_count = ledger.global_states[app_id][b'total_power_count']

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
    box_index, array_index = get_latest_checkpoint_indexes(ledger, app_id)
    boxes = ledger.boxes[app_id]
    timestamp = parse_box_total_power(boxes[TOTAL_POWERS + itob(box_index)])[array_index]["timestamp"]
    return timestamp


def get_slope(locked_amount):
    return locked_amount * TWO_TO_THE_64 // MAX_LOCK_TIME

def get_bias(slope, time_delta):
    assert time_delta >= 0
    return (slope * time_delta) // TWO_TO_THE_64

def get_voting_power(slope, remaining_time):
    return slope * remaining_time // 2**64


def get_start_time_of_day(value):
    return (value // DAY) * DAY

def get_start_timestamp_of_week(value):
    return (value // WEEK) * WEEK

def get_required_minimum_balance_of_box(box_name, box_size):
    return 2500 + 400 * (len(box_name) + box_size)