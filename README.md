# pbcon

This Project includes convenience tools to work with
[pybricksdev](https://github.com/pybricks/pybricksdev) for the _LEGO SPIKE
Prime_ and _LEGO Mindstorms Robot Inventor_ hub.


## Major Differences to the `pybricksdev` Tool

The main tool `pbcon` differs from `pybricksdev` in the following notable ways: 

* It only supports the _Lego Spike_ and _Lego Mindstorms Inventor_ hubs.

* The name of the hub is a mandatory argument: unintentional connections are very unlikely. 

* It keeps BLE connections open once connected and by that facilitates:
    * continuous uploads to the hub,
    * displaying output from the hub,
    * makes hijacking the connection to the hub harder.


## Installation

    pip install git+https://github.com/DrTom/pbcon.git@main

## Usage

See `pbcon -h`. 

## License

MIT, the same as for _pybricksdev_.
