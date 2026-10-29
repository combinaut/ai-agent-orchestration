# Changelog

## [1.1.0](https://github.com/combinaut/ai-agent-orchestration/compare/v1.0.0...v1.1.0) (2026-10-29)


### Features

* **architect:** evidence-format reviewer template ([#125](https://github.com/combinaut/ai-agent-orchestration/issues/125)) ([fc8fa6b](https://github.com/combinaut/ai-agent-orchestration/commit/fc8fa6baba9b2c9e49bef187f5f97d8e7531c2ff))
* **architect:** start Codex workers with hook trust bypassed ([#120](https://github.com/combinaut/ai-agent-orchestration/issues/120)) ([ae0bf05](https://github.com/combinaut/ai-agent-orchestration/commit/ae0bf051c800a7b39482fe1e53aacdf79c5855ad))
* **heavy-slot:** --note and feed lines for wait, fail, kill and long runs ([#117](https://github.com/combinaut/ai-agent-orchestration/issues/117)) ([33d6c56](https://github.com/combinaut/ai-agent-orchestration/commit/33d6c56fd81856669140e90a3b113b3b417af8a6))
* **pi:** pick a pi model per task from models.pi_models ([#91](https://github.com/combinaut/ai-agent-orchestration/issues/91)) ([5803bfc](https://github.com/combinaut/ai-agent-orchestration/commit/5803bfce15274ca2cca04a3921664a6a3585439c))
* **pr-tabs:** i18n in en, es, de, fr, pt and rename to Orch Tabs ([#95](https://github.com/combinaut/ai-agent-orchestration/issues/95)) ([00b5f98](https://github.com/combinaut/ai-agent-orchestration/commit/00b5f9801dfd2390d01b03578502e4351b90a4a4))
* **pr-tabs:** icon for Orch Tabs ([#97](https://github.com/combinaut/ai-agent-orchestration/issues/97)) ([8f64780](https://github.com/combinaut/ai-agent-orchestration/commit/8f64780c348f357f6e5d910cae4dd25bcfbdb086))
* **pr-tabs:** jump from a PR to its Herdr pane ([#100](https://github.com/combinaut/ai-agent-orchestration/issues/100)) ([d081c80](https://github.com/combinaut/ai-agent-orchestration/commit/d081c803eef33fd208ae9c8c0becccbe7d8ac7c8))
* **pr-tabs:** open a pasted list of PRs ([#119](https://github.com/combinaut/ai-agent-orchestration/issues/119)) ([f53bed6](https://github.com/combinaut/ai-agent-orchestration/commit/f53bed6736cd38d7be05cc829950d4f098e8719d))
* **pr-tabs:** search, review and manage PR tabs ([#107](https://github.com/combinaut/ai-agent-orchestration/issues/107)) ([a499b81](https://github.com/combinaut/ai-agent-orchestration/commit/a499b811137371a893ba5bd41a6c386f6791d78a))
* **providers:** add cursor family for Grok via Cursor CLI ([#113](https://github.com/combinaut/ai-agent-orchestration/issues/113)) ([5633c51](https://github.com/combinaut/ai-agent-orchestration/commit/5633c51f762b9db00eb6afca078c4dfa9c456f95))
* **questions:** answer with the asker's own option id and label ([#102](https://github.com/combinaut/ai-agent-orchestration/issues/102)) ([ee724dd](https://github.com/combinaut/ai-agent-orchestration/commit/ee724ddbf76e0756872d0683c5ae7d5b8f9ddbca))
* **recycle:** soft and hard worker thresholds ([#85](https://github.com/combinaut/ai-agent-orchestration/issues/85)) ([a63e3e2](https://github.com/combinaut/ai-agent-orchestration/commit/a63e3e22c37a2e3af6352b2e8f4f0a2e77971773))
* **settings:** effort level per role ([#116](https://github.com/combinaut/ai-agent-orchestration/issues/116)) ([c98dc5b](https://github.com/combinaut/ai-agent-orchestration/commit/c98dc5bbbd42ce966d878a8423bed360bf6681e0))
* **settings:** make Codex smart reasoning effort configurable ([#106](https://github.com/combinaut/ai-agent-orchestration/issues/106)) ([eaed8f3](https://github.com/combinaut/ai-agent-orchestration/commit/eaed8f3d2f3bf37277c1186bd8b40b87aa9fc3d6))
* **skills:** finalize skills in every repo ([#89](https://github.com/combinaut/ai-agent-orchestration/issues/89)) ([fc4ad88](https://github.com/combinaut/ai-agent-orchestration/commit/fc4ad885967fa633b4e01b0706355a62e705f03b))
* **todo:** Dismiss replaces Ignore and keeps the action wanted ([#135](https://github.com/combinaut/ai-agent-orchestration/issues/135)) ([fea57aa](https://github.com/combinaut/ai-agent-orchestration/commit/fea57aa52bead593a884c44feab6255ceca04257))
* **usage:** cost ledger per closed session and orch-usage weekly read ([#92](https://github.com/combinaut/ai-agent-orchestration/issues/92)) ([a5e67f7](https://github.com/combinaut/ai-agent-orchestration/commit/a5e67f75fbe73567b50f5a59db7836bb791c8fc4))
* **watchers:** COMMENTED event for a review whose findings are only in its body ([#88](https://github.com/combinaut/ai-agent-orchestration/issues/88)) ([0760a72](https://github.com/combinaut/ai-agent-orchestration/commit/0760a72249d9649a541c03842b9c9cf04470a171))


### Bug Fixes

* **codex-start:** use the configured smart reasoning effort ([#127](https://github.com/combinaut/ai-agent-orchestration/issues/127)) ([de593d2](https://github.com/combinaut/ai-agent-orchestration/commit/de593d271261675884a01d57a10728084f6b62f9))
* follow-ups from the [#117](https://github.com/combinaut/ai-agent-orchestration/issues/117), [#106](https://github.com/combinaut/ai-agent-orchestration/issues/106) and [#92](https://github.com/combinaut/ai-agent-orchestration/issues/92) reviews ([#131](https://github.com/combinaut/ai-agent-orchestration/issues/131)) ([e71e9ad](https://github.com/combinaut/ai-agent-orchestration/commit/e71e9ad5cc201c834f814d5bbdc1192a8dc08932))
* **guard-bash:** close the printf and append fail-open from [#121](https://github.com/combinaut/ai-agent-orchestration/issues/121) ([#132](https://github.com/combinaut/ai-agent-orchestration/issues/132)) ([140d226](https://github.com/combinaut/ai-agent-orchestration/commit/140d22685d7e0cb3590b13d3ed89bd3a116ed977))
* **guard-bash:** scan scripts a command writes and runs for broad kills ([#121](https://github.com/combinaut/ai-agent-orchestration/issues/121)) ([dad01b0](https://github.com/combinaut/ai-agent-orchestration/commit/dad01b0ae44b812a24d9f46b6dc79959fb41b0c3))
* **orch-signal:** recognize Codex worker panes in the sandbox ([#114](https://github.com/combinaut/ai-agent-orchestration/issues/114)) ([495b421](https://github.com/combinaut/ai-agent-orchestration/commit/495b421908b65311c9df34a56c8adbd18c7dc117))
* **pr-tabs:** group PRs listed in an architect's status file ([#109](https://github.com/combinaut/ai-agent-orchestration/issues/109)) ([a5c7120](https://github.com/combinaut/ai-agent-orchestration/commit/a5c71204e65aac6f076e4eb1e01fd5e414b18ed3))
* **pr-tabs:** keep expanded PR rows inside the group card ([#104](https://github.com/combinaut/ai-agent-orchestration/issues/104)) ([1fc5fe8](https://github.com/combinaut/ai-agent-orchestration/commit/1fc5fe8d0a85535dd61bd0b6ee60856c51d1e690))
* **pr-tabs:** never open a duplicate PR tab ([#105](https://github.com/combinaut/ai-agent-orchestration/issues/105)) ([c5308db](https://github.com/combinaut/ai-agent-orchestration/commit/c5308dbcbdbe02084e6013086210edbdc20af38e))
* **pr-tabs:** terminal icon focuses the owning architect ([#111](https://github.com/combinaut/ai-agent-orchestration/issues/111)) ([d2e4829](https://github.com/combinaut/ai-agent-orchestration/commit/d2e48290591b12990a61ce3f3429f034353e7257))
* **review-prs:** clear a watched PR only after a confirmed absence ([#134](https://github.com/combinaut/ai-agent-orchestration/issues/134)) ([dfd6aee](https://github.com/combinaut/ai-agent-orchestration/commit/dfd6aee139d027778032c816ca4b03f9a2d29b6e))
* **review-prs:** fetch a fork head when pull/&lt;n&gt;/head lags ([#128](https://github.com/combinaut/ai-agent-orchestration/issues/128)) ([71328c0](https://github.com/combinaut/ai-agent-orchestration/commit/71328c084ef46863f0bf01c2595146598f92fdf7))
* **review-prs:** post Codex reviews with one plain gh api call ([#133](https://github.com/combinaut/ai-agent-orchestration/issues/133)) ([004be51](https://github.com/combinaut/ai-agent-orchestration/commit/004be51919d582d862e9d026e0e25cf3708a97f2))
* **review-prs:** release the claim when a review pane never starts ([#118](https://github.com/combinaut/ai-agent-orchestration/issues/118)) ([54fc9a2](https://github.com/combinaut/ai-agent-orchestration/commit/54fc9a25cc6bd520ea2d0ac348ecba82b4c2cef2))
* **todo:** Escape ends the popup and a new open replaces a stale one ([#136](https://github.com/combinaut/ai-agent-orchestration/issues/136)) ([76d97e6](https://github.com/combinaut/ai-agent-orchestration/commit/76d97e6fd90d38d33feef973c876f44349ae3798))
* **watchers:** count sleep in loop intervals ([#130](https://github.com/combinaut/ai-agent-orchestration/issues/130)) ([448bd15](https://github.com/combinaut/ai-agent-orchestration/commit/448bd157ca636a4f6402272af176b6db6b7f954c))
* **watchers:** match only exact architect identities ([#112](https://github.com/combinaut/ai-agent-orchestration/issues/112)) ([fa89b2c](https://github.com/combinaut/ai-agent-orchestration/commit/fa89b2c721d6e3fb492897f268b7d167f20b92b9))


### Performance Improvements

* **pr-tabs:** generate Aura styles during the build ([#94](https://github.com/combinaut/ai-agent-orchestration/issues/94)) ([50ce0f9](https://github.com/combinaut/ai-agent-orchestration/commit/50ce0f9ebe744dd93a1bd61d00bc15f980048767))

## 1.0.0 (2026-10-24)


### Features

* **config:** move settings to orchestration.toml and per-repo files ([#4](https://github.com/combinaut/ai-agent-orchestration/issues/4)) ([228d7bf](https://github.com/combinaut/ai-agent-orchestration/commit/228d7bf78eb879aaa8a535f5565224df85c1a312))
* **coordinator:** add user question panels ([#31](https://github.com/combinaut/ai-agent-orchestration/issues/31)) ([5b9f904](https://github.com/combinaut/ai-agent-orchestration/commit/5b9f904ccf22331a3e4d32b58f18694f507dd77f))
* **coordinator:** alert architects when workers wait for follow-up ([#48](https://github.com/combinaut/ai-agent-orchestration/issues/48)) ([93389d8](https://github.com/combinaut/ai-agent-orchestration/commit/93389d8150479cdb1d0e6680c60d29eb7932f652))
* **coordinator:** close done workers, WORKER IDLE after 2 h ([#47](https://github.com/combinaut/ai-agent-orchestration/issues/47)) ([deea160](https://github.com/combinaut/ai-agent-orchestration/commit/deea1601f879ef559a71c0eb82404d1e5e311a93))
* **coordinator:** close the to-do popup with Esc or q ([#36](https://github.com/combinaut/ai-agent-orchestration/issues/36)) ([18ae59e](https://github.com/combinaut/ai-agent-orchestration/commit/18ae59e42abb364d1f12a70591d79eaddb2dfbb1))
* **coordinator:** Dismiss and Remind actions on the Q panel ([#46](https://github.com/combinaut/ai-agent-orchestration/issues/46)) ([9354d49](https://github.com/combinaut/ai-agent-orchestration/commit/9354d496bebcba1a2288f82b6916204415105efa))
* **coordinator:** open the to-do pane at start and install the popup by default ([#50](https://github.com/combinaut/ai-agent-orchestration/issues/50)) ([576b98c](https://github.com/combinaut/ai-agent-orchestration/commit/576b98c5c0e6deeaf0798905806814a1da926dac))
* **coordinator:** route worker recycles to the owning architect ([#44](https://github.com/combinaut/ai-agent-orchestration/issues/44)) ([5aa7ef2](https://github.com/combinaut/ai-agent-orchestration/commit/5aa7ef20b8baab2bd0043ffb70c6cc9116202016))
* **coordinator:** to-do view with Open, Ignore and Reminder buttons ([#33](https://github.com/combinaut/ai-agent-orchestration/issues/33)) ([a232c40](https://github.com/combinaut/ai-agent-orchestration/commit/a232c40645a24dad79fa560906571e71b301850e))
* **herdr:** opt-in to-do popup titled 'Your to-do' on prefix+t ([#39](https://github.com/combinaut/ai-agent-orchestration/issues/39)) ([867f5ed](https://github.com/combinaut/ai-agent-orchestration/commit/867f5ed76c724644e933f494b1f9c874432d71ed))
* **install:** --links-only links new files without touching settings or services ([#55](https://github.com/combinaut/ai-agent-orchestration/issues/55)) ([b73b6c0](https://github.com/combinaut/ai-agent-orchestration/commit/b73b6c056b54da54fa02aaad04d0bddedc6013c1))
* **install:** add the bind-self.py allow rules to settings.json ([#37](https://github.com/combinaut/ai-agent-orchestration/issues/37)) ([f6a02db](https://github.com/combinaut/ai-agent-orchestration/commit/f6a02dba746c98f2d27e2136593073c21f99e477))
* **install:** keep the CLAUDE.md orchestration block current ([#29](https://github.com/combinaut/ai-agent-orchestration/issues/29)) ([63208e7](https://github.com/combinaut/ai-agent-orchestration/commit/63208e7ae8635ba20d64e63efbb1d3811c4bfea1))
* **install:** one-line get.sh installer ([#72](https://github.com/combinaut/ai-agent-orchestration/issues/72)) ([c2a045d](https://github.com/combinaut/ai-agent-orchestration/commit/c2a045deb8ff5b346ee4aa78f9d5a49c169a4594))
* **install:** plain curl | bash install line ([#74](https://github.com/combinaut/ai-agent-orchestration/issues/74)) ([fc20bb5](https://github.com/combinaut/ai-agent-orchestration/commit/fc20bb5aced8558cfb1876100cd01e35753dec35))
* **install:** short curl one-liner, herdr env by default, shorter README ([#2](https://github.com/combinaut/ai-agent-orchestration/issues/2)) ([cae310b](https://github.com/combinaut/ai-agent-orchestration/commit/cae310b5efcef816dddf2e9e39c8fc93aae47365))
* **legacy:** repo-defined scan rules ([#17](https://github.com/combinaut/ai-agent-orchestration/issues/17)) ([2ef77bc](https://github.com/combinaut/ai-agent-orchestration/commit/2ef77bc8f5361651d8b6a2a3ee08f2456aa7073c))
* pi workers on a local model or DeepSeek, DeepSeek opt-in per repo ([#38](https://github.com/combinaut/ai-agent-orchestration/issues/38)) ([ef0ae1d](https://github.com/combinaut/ai-agent-orchestration/commit/ef0ae1d0422f1805a5f376edbb578f6b821f3fce))
* **pr-tabs:** group tabs by owning architect ([#15](https://github.com/combinaut/ai-agent-orchestration/issues/15)) ([d43a9cf](https://github.com/combinaut/ai-agent-orchestration/commit/d43a9cf57be4ef63b526ead719ac4d13c13aa3e9))
* **pr-tabs:** pick the repo for a bare PR number ([#78](https://github.com/combinaut/ai-agent-orchestration/issues/78)) ([64177ba](https://github.com/combinaut/ai-agent-orchestration/commit/64177ba95ad55fefc0bcb26bd6d712ec31a628c0))
* providers.enabled setting to switch worker families off ([#56](https://github.com/combinaut/ai-agent-orchestration/issues/56)) ([ead6556](https://github.com/combinaut/ai-agent-orchestration/commit/ead65568a2f720134e2236b19621e081eae68290))
* **providers:** haiku tier, lightest-tier routing, quieter messages ([#66](https://github.com/combinaut/ai-agent-orchestration/issues/66)) ([5137103](https://github.com/combinaut/ai-agent-orchestration/commit/513710309be36655f7418ea9993755706cb7152e))
* **providers:** one Codex model is enough; Claude tier allow list ([#62](https://github.com/combinaut/ai-agent-orchestration/issues/62)) ([2b4b2c2](https://github.com/combinaut/ai-agent-orchestration/commit/2b4b2c2a1ffa01364301b04667da1fd08c763236))
* remote_control.architects setting, on by default ([#54](https://github.com/combinaut/ai-agent-orchestration/issues/54)) ([042bed5](https://github.com/combinaut/ai-agent-orchestration/commit/042bed5b7ace262152c958d5dab881787d96226e))
* **review-prs:** ship the review-prs skill from this repo ([#21](https://github.com/combinaut/ai-agent-orchestration/issues/21)) ([c36f90b](https://github.com/combinaut/ai-agent-orchestration/commit/c36f90b123cda9269843c468247deed0acf2223b))
* **settings:** run repo finalize commands before a PR is done ([#24](https://github.com/combinaut/ai-agent-orchestration/issues/24)) ([0201531](https://github.com/combinaut/ai-agent-orchestration/commit/0201531898a8d8d2252c7fb4892b18a17b2f734a))
* **startup:** compact agent list and settings print for session startup ([#43](https://github.com/combinaut/ai-agent-orchestration/issues/43)) ([4e56b52](https://github.com/combinaut/ai-agent-orchestration/commit/4e56b5249187ffbc61d8899c8366fe729ee462c6))
* **statusline:** link the repo name and branch to their web pages ([#64](https://github.com/combinaut/ai-agent-orchestration/issues/64)) ([ccd0aba](https://github.com/combinaut/ai-agent-orchestration/commit/ccd0aba69393b8c6e71e4c0d8218853cdff51cd7))
* **todo:** clicking an architect heading focuses its pane ([#58](https://github.com/combinaut/ai-agent-orchestration/issues/58)) ([08a986a](https://github.com/combinaut/ai-agent-orchestration/commit/08a986a1d0673a9b1cdfa68a28ea272f86a80bec))
* **todo:** remove the Open button; Enter focuses the architect ([#61](https://github.com/combinaut/ai-agent-orchestration/issues/61)) ([8e8e1c7](https://github.com/combinaut/ai-agent-orchestration/commit/8e8e1c705b0b4acd52cc31cc5636d8db88d4962c))
* **watchers:** discover repos and idle architects from open work ([#16](https://github.com/combinaut/ai-agent-orchestration/issues/16)) ([214f1aa](https://github.com/combinaut/ai-agent-orchestration/commit/214f1aaa5f0547a3fbf0151995e20ec70b1375ae))
* **watchers:** make focus and legacy-scan patterns settings ([#1](https://github.com/combinaut/ai-agent-orchestration/issues/1)) ([4f19811](https://github.com/combinaut/ai-agent-orchestration/commit/4f198115578f9644b2f6e0bb110dfa400dcafdf4))
* **watchers:** only action lines reach the feed and native delivery ([#65](https://github.com/combinaut/ai-agent-orchestration/issues/65)) ([285ea8a](https://github.com/combinaut/ai-agent-orchestration/commit/285ea8ac1dd28853b5ec35ea53c2d954c96c169b))


### Bug Fixes

* **balance:** report a READFAIL only after 3 unreadable scans and log why a read failed ([#45](https://github.com/combinaut/ai-agent-orchestration/issues/45)) ([894d687](https://github.com/combinaut/ai-agent-orchestration/commit/894d68729d4e99f18b007bd1b0616f8444ae6cac))
* **coordinator:** close question panes by their exact label ([#35](https://github.com/combinaut/ai-agent-orchestration/issues/35)) ([7829aa5](https://github.com/combinaut/ai-agent-orchestration/commit/7829aa55df3436ee180e51e47ec884a1c48d2330))
* **install:** make the one-liner fail when the download fails or is empty ([#11](https://github.com/combinaut/ai-agent-orchestration/issues/11)) ([9ee860b](https://github.com/combinaut/ai-agent-orchestration/commit/9ee860b6c90827bd9d8521c8ae4516e835dc1f73))
* **install:** print the bootout step whenever a job stays loaded ([#26](https://github.com/combinaut/ai-agent-orchestration/issues/26)) ([7d75994](https://github.com/combinaut/ai-agent-orchestration/commit/7d75994bec56f37d6ce3df790a72c1f101a826f4))
* **install:** start macOS watcher jobs after bootstrap ([#22](https://github.com/combinaut/ai-agent-orchestration/issues/22)) ([abbd7d4](https://github.com/combinaut/ai-agent-orchestration/commit/abbd7d46396135c7848dceeb8f505cb09bbd93da))
* **install:** wait for the old Radar daemon to exit and verify the new one runs ([#5](https://github.com/combinaut/ai-agent-orchestration/issues/5)) ([d2e2442](https://github.com/combinaut/ai-agent-orchestration/commit/d2e2442c9670dece0b346fc93818ad78cedad63a))
* **legacy:** run legacy-scan and legacy-map under bash when started with zsh or sh ([#19](https://github.com/combinaut/ai-agent-orchestration/issues/19)) ([f330c6d](https://github.com/combinaut/ai-agent-orchestration/commit/f330c6d9fd6f73dbf75bf99dcf9b525b299635ff))
* providers follow-ups from the [#56](https://github.com/combinaut/ai-agent-orchestration/issues/56) review ([#60](https://github.com/combinaut/ai-agent-orchestration/issues/60)) ([7ff907d](https://github.com/combinaut/ai-agent-orchestration/commit/7ff907d3ef0fdc7adeabe71c9aebd444e1ddbd40))
* **review-prs:** confirm missing review requests ([#53](https://github.com/combinaut/ai-agent-orchestration/issues/53)) ([0a422c8](https://github.com/combinaut/ai-agent-orchestration/commit/0a422c83614ef791ae80d9efc2b6db90855bd3dd))
* **review-prs:** ignore transient unknown merge states ([#57](https://github.com/combinaut/ai-agent-orchestration/issues/57)) ([6731f12](https://github.com/combinaut/ai-agent-orchestration/commit/6731f12ddded4aafc7c1396b0866c20e640694ed))
* **test:** make the soft-deadline replay test independent of wall-clock speed ([#41](https://github.com/combinaut/ai-agent-orchestration/issues/41)) ([1d2a7a6](https://github.com/combinaut/ai-agent-orchestration/commit/1d2a7a6aafe19f92ede1ae681d093072155ce540))
* **todo:** put the age tag before the item text so a trailing URL stays clean ([#23](https://github.com/combinaut/ai-agent-orchestration/issues/23)) ([71a788d](https://github.com/combinaut/ai-agent-orchestration/commit/71a788d6baac00dff3ed0f9cabae6f8d0f5182ce))
* **todo:** skip placeholder lines such as (none) in the waits-on-the-user section ([#25](https://github.com/combinaut/ai-agent-orchestration/issues/25)) ([3e3bd99](https://github.com/combinaut/ai-agent-orchestration/commit/3e3bd9990034fc1ca5d4c7039068b6a3c96798e9))
* **watchers:** preserve pulls events and owners across incomplete reads ([#51](https://github.com/combinaut/ai-agent-orchestration/issues/51)) ([a8c824e](https://github.com/combinaut/ai-agent-orchestration/commit/a8c824e3b9fe1ceb997c8dfed66f17c91298428c))
* **watchers:** watch the upstream repo of fork clones and give its PRs an owner ([#49](https://github.com/combinaut/ai-agent-orchestration/issues/49)) ([cbe599a](https://github.com/combinaut/ai-agent-orchestration/commit/cbe599ab6bfea83c25da126ebfd7d1d611d58c00))
