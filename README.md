# Tinyman Governance

This repo contains the contracts that form the Tinyman Governance system.

### Docs

The governance system is described in detail in the following document:
[Tinyman Governance Specification](docs/tinyman_governance_protocol_specification.pdf)

User docs for Tinyman Governance can be found at [docs.tinyman.org](https://docs.tinyman.org).


### Contracts
The contracts are written in [Tealish](https://github.com/tinymanorg/tealish).
The specific version of Tealish is https://github.com/tinymanorg/tealish/tree/df4b0130e0c23a3498dda2b2f1a21b3249530813.

The annotated TEAL outputs and compiled bytecode are available in the build subfolders.


### Audits

Audit reports from independent reviewers can be found in the [audits](audits/) directory.


### Security
#### Reporting a Vulnerability
Reports of potential flaws must be responsibly disclosed to security@tinyman.org. Do not share details with anyone else until notified to do so by the team.


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

### Licensing

The contents of this repository are licensed under the Business Source License 1.1 (BUSL-1.1), see [LICENSE](LICENSE).
