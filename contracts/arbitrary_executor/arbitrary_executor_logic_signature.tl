#pragma version 8
#tealish version git+https://github.com/Hipo/tealish.git@e8d1b27620220bc4e520d7d3b6d62523e13a7723

# Assert that Gtxn[0] is an appcall to execution validator
assert(Gtxn[0].ApplicationID == 10000)

assert(Txn.GroupIndex == 1)

if Gtxn[0].ApplicationArgs[0] == "validate_transaction":
    assert(Global.GroupSize == 2)
elif Gtxn[0].ApplicationArgs[0] == "validate_group":
    assert(Global.GroupSize >= 2)
else:
    exit(0)
end

# Assert that there is no rekeying
int start = 0
int end = Global.GroupSize
for i in start:end:
    assert(Gtxn[i].RekeyTo == Global.ZeroAddress)
end
exit(1)
