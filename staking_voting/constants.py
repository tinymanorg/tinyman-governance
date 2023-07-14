MAX_OPTION_COUNT = 16
BYTES_ZERO = b'\x00'
BYTES_ONE = b'\x01'

# Global States
TINY_ASSET_ID_KEY = b'tiny_asset_id'
LOCKING_APP_ID_KEY = b'locking_app_id'
PROPOSAL_ID_COUNTER_KEY = b'proposal_id_counter'
VOTING_DELAY_KEY = b'voting_delay'
VOTING_DURATION_KEY = b'voting_duration'
MANAGER_KEY = b'manager'
PROPOSAL_MANAGER_KEY = b'proposal_manager'

# Box
PROPOSAL_BOX_PREFIX = b'p'
VOTE_BOX_PREFIX = b'v'
ATTENDANCE_BOX_PREFIX = b'a'

PROPOSAL_ASSET_BOX_SIZE = 8
ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE = 24
