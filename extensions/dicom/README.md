# purplship.dicom

This package is a Dicom extension of the [purplship](https://pypi.org/project/purplship) multi carrier shipping SDK.

## Requirements

`Python 3.6+`

## Installation

```bash
pip install purplship.dicom
```

## Usage

```python
import purplship
from purplship.mappers.dicom.settings import Settings


# Initialize a carrier gateway
canadapost = purplship.gateway["dicom"].create(
    Settings(
        ...
    )
)
```

Check the [Purplship Mutli-carrier SDK docs](https://sdk.purplship.com) for Shipping API requests
