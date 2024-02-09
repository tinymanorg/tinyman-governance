#pragma version 9
#tealish version git+https://github.com/Hipo/tealish.git@c4f8c12b36dbcc8b63844df5f818164925735087

const int EXECUTION_VALIDATOR_APP_ID = 10000

# Assert that Gtxn[0] is an appcall to execution validator
assert(Gtxn[0].ApplicationID == EXECUTION_VALIDATOR_APP_ID)

assert(Txn.GroupIndex == 1)

assert(Txn.RekeyTo == Global.ZeroAddress)

exit(1)
