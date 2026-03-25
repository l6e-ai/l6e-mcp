# Repository Coverage



| Name                                            |    Stmts |     Miss |   Cover |   Missing |
|------------------------------------------------ | -------: | -------: | ------: | --------: |
| src/l6e\_mcp/\_\_init\_\_.py                    |        3 |        0 |    100% |           |
| src/l6e\_mcp/config.py                          |      112 |       12 |     89% |39, 49-51, 76-77, 111, 127, 148-149, 187-188 |
| src/l6e\_mcp/contracts/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/l6e\_mcp/contracts/correlation\_envelope.py |       11 |       11 |      0% |      2-18 |
| src/l6e\_mcp/contracts/exactness.py             |       12 |        0 |    100% |           |
| src/l6e\_mcp/contracts/mode\_coverage.py        |       20 |        1 |     95% |        14 |
| src/l6e\_mcp/core/\_\_init\_\_.py               |        0 |        0 |    100% |           |
| src/l6e\_mcp/core/authorization.py              |       67 |        3 |     96% |107, 123, 178 |
| src/l6e\_mcp/core/calibration\_cache.py         |       45 |        0 |    100% |           |
| src/l6e\_mcp/core/exactness.py                  |       29 |        0 |    100% |           |
| src/l6e\_mcp/core/remote\_authorize.py          |       50 |       20 |     60% |32-38, 43-56 |
| src/l6e\_mcp/core/session\_report\_worker.py    |       38 |        1 |     97% |        45 |
| src/l6e\_mcp/core/status\_telemetry.py          |       53 |        1 |     98% |        60 |
| src/l6e\_mcp/outbox.py                          |      105 |        8 |     92% |88-89, 106-107, 135-137, 166 |
| src/l6e\_mcp/overhead.py                        |       18 |        1 |     94% |        31 |
| src/l6e\_mcp/server.py                          |      307 |       18 |     94% |92, 99, 102, 110, 113, 171, 186-188, 214, 430, 432, 668, 732, 778-780, 784 |
| src/l6e\_mcp/session\_store.py                  |       48 |        4 |     92% |98, 171, 174, 194 |
| src/l6e\_mcp/store/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/l6e\_mcp/store/\_connection.py              |       47 |        0 |    100% |           |
| src/l6e\_mcp/store/\_migrations.py              |       67 |        1 |     99% |        47 |
| src/l6e\_mcp/store/\_serialization.py           |       34 |        1 |     97% |        80 |
| src/l6e\_mcp/store/calls.py                     |      130 |        5 |     96% |171, 240, 319, 334, 372 |
| src/l6e\_mcp/store/diagnostics.py               |       19 |        0 |    100% |           |
| src/l6e\_mcp/store/schema.py                    |       17 |        0 |    100% |           |
| src/l6e\_mcp/store/sessions.py                  |      107 |        3 |     97% |135, 189, 212 |
| src/l6e\_mcp/store/summary.py                   |       72 |        5 |     93% |119, 122, 124, 126, 151 |
| src/l6e\_mcp/tools/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/l6e\_mcp/transport/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/l6e\_mcp/transport/http/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| **TOTAL**                                       | **1411** |   **95** | **93%** |           |


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