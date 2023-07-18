from algosdk import abi

from common.event import Event

MAX_LOCK_TIME = 126144000
MIN_LOCK_AMOUNT = 10000000
MIN_LOCK_AMOUNT_INCREMENT = 10000000
TWO_TO_THE_64 = 2 ** 64
# TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"

# Global states
TINY_ASSET_ID_KEY = b'tiny_asset_id'
TOTAL_LOCKED_AMOUNT_KEY = b'total_locked_amount'
TOTAL_POWER_COUNT_KEY = b'total_power_count'
CREATION_TIMESTAMP_KEY = b'creation_timestamp'

# Boxes
TOTAL_POWERS = b'tp'
SLOPE_CHANGES = b'sc'

ACCOUNT_STATE_SIZE = 24
SLOPE_CHANGE_SIZE = 16

ACCOUNT_POWER_SIZE = 48
ACCOUNT_POWER_BOX_SIZE = 1008
ACCOUNT_POWER_BOX_ARRAY_LEN = 21

TOTAL_POWER_SIZE = 48
TOTAL_POWER_BOX_SIZE = 1008
TOTAL_POWER_BOX_ARRAY_LEN = 21

# 100_000 Default
# 100_000 Opt-in
# Box
# https://developer.algorand.org/docs/get-details/dapps/smart-contracts/apps/?from_query=box#minimum-balance-requirement-for-boxes
# 2500 + 400 * (len(n)+s)
# 2_500 Box
# 411_200 = 400 * (20 + 1008)
VAULT_APP_MINIMUM_BALANCE_REQUIREMENT = 613_700

# Events
event_create_lock = Event(
    name="create_lock",
    args=[
        abi.Argument(arg_type="address", name="user_address"),
        abi.Argument(arg_type="uint64", name="locked_amount"),
        abi.Argument(arg_type="uint64", name="lock_end_time"),
    ]
)

event_increase_lock_amount = Event(
    name="increase_lock_amount",
    args=[
        abi.Argument(arg_type="address", name="user_address"),
        abi.Argument(arg_type="uint64", name="locked_amount"),
        abi.Argument(arg_type="uint64", name="lock_end_time"),
        abi.Argument(arg_type="uint64", name="amount_delta"),
    ]
)

event_extend_lock_end_time = Event(
    name="extend_lock_end_time",
    args=[
        abi.Argument(arg_type="address", name="user_address"),
        abi.Argument(arg_type="uint64", name="locked_amount"),
        abi.Argument(arg_type="uint64", name="lock_end_time"),
        abi.Argument(arg_type="uint64", name="time_delta"),
    ]
)

event_withdraw = Event(
    name="withdraw",
    args=[
        abi.Argument(arg_type="address", name="user_address"),
        abi.Argument(arg_type="uint64", name="amount"),
    ]
)

event_account_power = Event(
    name="account_power",
    args=[
        abi.Argument(arg_type="address", name="user_address"),
        abi.Argument(arg_type="uint64", name="index"),
        abi.Argument(arg_type="uint64", name="bias"),
        abi.Argument(arg_type="uint64", name="timestamp"),
        abi.Argument(arg_type="byte[16]", name="slope"),
        abi.Argument(arg_type="byte[16]", name="cumulative_power"),
    ]
)

event_total_power = Event(
    name="total_power",
    args=[
        abi.Argument(arg_type="uint64", name="index"),
        abi.Argument(arg_type="uint64", name="bias"),
        abi.Argument(arg_type="uint64", name="timestamp"),
        abi.Argument(arg_type="byte[16]", name="slope"),
        abi.Argument(arg_type="byte[16]", name="cumulative_power"),
    ]
)

event_slope_change = Event(
    name="slope_change",
    args=[
        abi.Argument(arg_type="uint64", name="timestamp"),
        abi.Argument(arg_type="byte[16]", name="slope"),
    ]
)

vault_events = [
    event_create_lock,
    event_increase_lock_amount,
    event_extend_lock_end_time,
    event_withdraw,
    event_account_power,
    event_total_power,
    event_slope_change
]
