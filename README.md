# Repository Coverage



| Name                                            |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------------ | -------: | -------: | ------: | --------: |
| src/l6e\_mcp/\_\_init\_\_.py                    |        1 |        0 |    100% |           |
| src/l6e\_mcp/calibration/\_\_init\_\_.py        |        0 |        0 |    100% |           |
| src/l6e\_mcp/calibration/config.py              |      101 |        0 |    100% |           |
| src/l6e\_mcp/config.py                          |       54 |        6 |     89% |32-34, 47-48, 82 |
| src/l6e\_mcp/contracts/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/l6e\_mcp/contracts/correlation\_envelope.py |       11 |       11 |      0% |      2-18 |
| src/l6e\_mcp/contracts/exactness.py             |       12 |        0 |    100% |           |
| src/l6e\_mcp/contracts/mode\_coverage.py        |       20 |        1 |     95% |        14 |
| src/l6e\_mcp/core/\_\_init\_\_.py               |        0 |        0 |    100% |           |
| src/l6e\_mcp/core/authorization.py              |       73 |        3 |     96% |126, 149, 218 |
| src/l6e\_mcp/core/calibration\_cache.py         |       35 |        0 |    100% |           |
| src/l6e\_mcp/core/exactness.py                  |       29 |        0 |    100% |           |
| src/l6e\_mcp/core/remote\_authorize.py          |       19 |        0 |    100% |           |
| src/l6e\_mcp/core/session\_report\_worker.py    |       38 |        1 |     97% |        45 |
| src/l6e\_mcp/core/status\_telemetry.py          |       53 |        1 |     98% |        60 |
| src/l6e\_mcp/outbox.py                          |      105 |        8 |     92% |88-89, 106-107, 135-137, 166 |
| src/l6e\_mcp/overhead.py                        |       18 |        1 |     94% |        32 |
| src/l6e\_mcp/server.py                          |      276 |       27 |     90% |79, 101, 124, 127, 153, 155, 158, 170-171, 177-181, 198, 201, 321-325, 407, 409, 610, 674, 720, 724 |
| src/l6e\_mcp/session\_store.py                  |       48 |        3 |     94% |169, 172, 192 |
| src/l6e\_mcp/store/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/l6e\_mcp/store/\_connection.py              |       26 |        0 |    100% |           |
| src/l6e\_mcp/store/\_migrations.py              |       43 |        0 |    100% |           |
| src/l6e\_mcp/store/\_serialization.py           |       34 |        1 |     97% |        80 |
| src/l6e\_mcp/store/calls.py                     |      124 |        5 |     96% |170, 239, 313, 328, 366 |
| src/l6e\_mcp/store/diagnostics.py               |       16 |        0 |    100% |           |
| src/l6e\_mcp/store/repositories.py              |       12 |        0 |    100% |           |
| src/l6e\_mcp/store/schema.py                    |        8 |        0 |    100% |           |
| src/l6e\_mcp/store/sessions.py                  |       99 |        3 |     97% |130, 182, 204 |
| src/l6e\_mcp/store/summary.py                   |       70 |        4 |     94% |120, 123, 125, 150 |
| src/l6e\_mcp/tools/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/l6e\_mcp/transport/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/l6e\_mcp/transport/http/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| **TOTAL**                                       | **1325** |   **75** | **94%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://github.com/l6e-ai/l6e-mcp/raw/python-coverage-comment-action-data/badge.svg)](https://github.com/l6e-ai/l6e-mcp/tree/python-coverage-comment-action-data)

This is the one to use if your repository is private or if you don't want to customize anything.



## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.