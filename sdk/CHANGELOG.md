# Change Log

## 2.1.1

### Changed

* Added `py-algorand-sdk==2.0.0` support. [#58](https://github.com/tinymanorg/tinyman-py-sdk/pull/58)
* Added `refresh` parameter to Tinyman V1 fetch functions (`fetch_mint_quote`, `fetch_burn_quote`, `fetch_fixed_input_swap_quote`, `fetch_fixed_output_swap_quote`). [#59](https://github.com/tinymanorg/tinyman-py-sdk/pull/59)


## 2.1.0

### Added

* Added Tinyman V2 (Mainnet) support.
* Added `client_name` attribute to `TinymanClient` classes. [#51](https://github.com/tinymanorg/tinyman-py-sdk/pull/51)
* Added note to application call transactions. The note (`tinyman/<v1|v2>:j{"origin":"<client-name>"}`) follows [Algorand Transaction Note Field Conventions ARC-2](https://github.com/algorandfoundation/ARCs/blob/main/ARCs/arc-0002.md). [#51](https://github.com/tinymanorg/tinyman-py-sdk/pull/51)
* Added `version` property and `generate_app_call_note` method to `TinymanClient` classes. [#51](https://github.com/tinymanorg/tinyman-py-sdk/pull/51)
* Added `get_version` and `generate_app_call_note` to `tinyman.utils`. [#51](https://github.com/tinymanorg/tinyman-py-sdk/pull/51)
* Improved error handling [#49](https://github.com/tinymanorg/tinyman-py-sdk/pull/49/files).
  - Added `TealishMap`.
  - Added `AlgodError`, `LogicError`, `OverspendError` exception classes.
  - Added `amm_approval.map.json` for V2.

## 2.0.0

### Added

* Added Tinyman V2 (Testnet) support (`tinyman.v2`).
* Added Staking support (`tinyman.staking`).
  - It allows creating commitment transaction by `prepare_commit_transaction` and tracking commitments by `parse_commit_transaction`.
* Added `calculate_price_impact` function to `tinyman.utils`.
* Improved `TransactionGroup` class.
  - Added `+` operator support for composability, it allows creating a new transaction group (`txn_group_1 + txn_group_2`).
  - Added `id` property, it returns the transactions group id.
  - Added `TransactionGroup.sign_with_logicsig` function and deprecated `TransactionGroup.sign_with_logicisg` because of the typo.

### Changed

* `get_program` (V1) is moved from `tinyman.utils` to `tinyman.v1.contracts`.
* `get_state_from_account_info` (V1) is moved from `tinyman.utils` to `tinyman.v1.utils`.

### Removed

* Deprecated `wait_for_confirmation` function is removed. `wait_for_confirmation` is added to [Algorand SDK](https://github.com/algorand/py-algorand-sdk).
* Drop Python 3.7 support.

