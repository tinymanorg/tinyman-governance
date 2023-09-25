import math

from tinyman.governance.vault.constants import TOTAL_POWER_BOX_ARRAY_LEN, ACCOUNT_POWER_BOX_ARRAY_LEN
from tinyman.governance.vault.storage import VaultAppGlobalState, parse_box_account_state, get_account_state_box_name, get_slope_change_box_name, parse_box_slope_change, TotalPower, get_total_power_box_name, parse_box_total_power, get_account_power_box_name, parse_box_account_power

from tests.constants import VAULT_APP_ID


def get_vault_app_global_state(ledger, app_id=None) -> VaultAppGlobalState:
    if app_id is None:
        app_id = VAULT_APP_ID
    return VaultAppGlobalState(**{key.decode(): value for key, value in ledger.global_states[app_id].items()})


def get_account_state(ledger, user_address):
    box_name = get_account_state_box_name(address=user_address)
    if box_name in ledger.boxes[VAULT_APP_ID]:
        return parse_box_account_state(ledger.boxes[VAULT_APP_ID][box_name])
    return None


def get_slope_change_at(ledger, timestamp):
    box_name = get_slope_change_box_name(timestamp=timestamp)
    if box_name in ledger.boxes[VAULT_APP_ID]:
        return parse_box_slope_change(ledger.boxes[VAULT_APP_ID][box_name])
    return None


def get_all_total_powers(ledger, total_power_count: int) -> list[TotalPower]:
    if total_power_count:
        box_count = math.ceil(total_power_count / TOTAL_POWER_BOX_ARRAY_LEN)
    else:
        box_count = 0

    total_powers = []
    for box_index in range(box_count):
        box_name = get_total_power_box_name(box_index=box_index)
        raw_box = ledger.boxes[VAULT_APP_ID][box_name]
        total_powers.extend(parse_box_total_power(raw_box))
    return total_powers


def get_account_powers(ledger, address: str, power_count=None):
    if power_count is None:
        if account_state := get_account_state(ledger, address):
            power_count = account_state.power_count

    if power_count:
        box_count = math.ceil(power_count / ACCOUNT_POWER_BOX_ARRAY_LEN)
    else:
        box_count = 0

    account_powers = []
    for box_index in range(box_count):
        box_name = get_account_power_box_name(address=address, box_index=box_index)
        raw_box = ledger.boxes[VAULT_APP_ID][box_name]
        account_powers.extend(parse_box_account_power(raw_box))
    return account_powers
