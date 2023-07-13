BIT_ZERO = b'\x00'
BIT_ONE = b'\x01'

# Global states
TINY_ASSET_ID_KEY = b'tiny_asset_id'
LOCKING_APP_ID_KEY = b'locking_app_id'
CREATION_TIMESTAMP_KEY = b'creation_timestamp'
REWARD_HISTORY_COUNT_KEY = b'reward_history_count'
MANAGER_KEY = b'manager'

# Boxes
REWARD_HISTORY_BOX_PREFIX = b'rh'

REWARD_SHEET_BOX_SIZE = 1024

REWARD_HISTORY_SIZE = 16
REWARD_HISTORY_BOX_SIZE = 256
REWARD_HISTORY_BOX_ARRAY_LEN = 16

# 100_000 Default
# 100_000 Opt-in
# Box
# https://developer.algorand.org/docs/get-details/dapps/smart-contracts/apps/?from_query=box#minimum-balance-requirement-for-boxes
# 2500 + 400 * (len(n)+s)
# 2_500 Box
# 413_600 = 400 * (10 + 1024)
REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT = 616_100