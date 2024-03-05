# tinyman-governance



### Installing Dependencies
Note: Mac OS & Linux Only

```
% python3 -m venv ~/envs/gov
% source ~/envs/gov/bin/activate
(gov) % pip install -r requirements.txt
(gov) % python -m algojig.check
```

We recommend using VS Code with this Tealish extension when reviewing contracts written in Tealish: https://github.com/thencc/TealishVSCLangServer/blob/main/tealish-language-server-1.0.0.vsix


### Running Tests

```
# Run all tests (this can take a while)
(gov) % python -m unittest -v

# Run a specific test
(gov) % python -m unittest -vk "VaultTestCase.test_create_app"
```

Note: The tests read the `.tl` Tealish source files from the contracts directories, not the `.teal` build files.


### Compiling the Contract Sources

```
# Compile each set of contracts to generate the `.teal` files in the `build` subdirectories:
(gov) % tealish compile contracts/vault
(gov) % tealish compile contracts/proposal_voting
(gov) % tealish compile contracts/staking_voting
(gov) % tealish compile contracts/rewards
```
