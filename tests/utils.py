from algosdk.encoding import decode_address
from tinyman.governance.rewards.constants import REWARD_HISTORY_SIZE, REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_HISTORY_BOX_PREFIX, REWARD_HISTORY_COUNT_KEY
from tinyman.governance.vault.constants import TOTAL_POWERS, TOTAL_POWER_COUNT_KEY, TOTAL_POWER_BOX_ARRAY_LEN, ACCOUNT_POWER_BOX_ARRAY_LEN
from tinyman.governance.vault.storage import parse_box_total_power, parse_box_account_power, parse_box_account_state
from tinyman.utils import bytes_to_int, int_to_bytes


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

def get_reward_history_index_at(ledger, app_id, timestamp):
    reward_history_index = None
    reward_history_count = ledger.global_states[app_id][REWARD_HISTORY_COUNT_KEY]

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


def get_app_box_names(ledger, app_id):
    if app_id in ledger.boxes:
        return list(ledger.boxes[app_id].keys())
    return list()

def get_first_app_call_txn(block_txns, ignore_budget_increase=True):
    for txn in block_txns:
        if txn[b"txn"][b"type"] == b"appl":
            if ignore_budget_increase:
                if txn[b"txn"][b"apaa"][0] != b"increase_budget":
                    return txn
            else:
                return txn
