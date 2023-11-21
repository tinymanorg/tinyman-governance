#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@e8d1b27620220bc4e520d7d3b6d62523e13a7723

# Assert that Gtxn[0] is an appcall to execution validator
assert(Gtxn[0].ApplicationID == 10000)

assert(Gtxn[0].ApplicationArgs[0] == "validate_transaction")

assert(Txn.GroupIndex == 1)

# Assert that there is no rekeying
assert(Gtxn[1].RekeyTo == Global.ZeroAddress)
exit(1)
