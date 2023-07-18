Instructions to run the script:

Pre-requisites: 

1. git clone the lattice-estimator repository (`git clone https://github.com/malb/lattice-estimator.git`)
2. Install python with `sudo apt install python3` (tested with 3.8.10)
3. Install sage with `sudo apt install sagemath` (tested with SageMath version 9.0)

Steps to run the script:
1. Clone the [openfhe-development](https://github.com/openfheorg/openfhe-development) repository and checkout the `dev` branch.
2. Follow the `Installation` section of the OpenFHE readme to build and install the library. Use the flag `WITH_NOISE_DEBUG` set to `ON` while running cmake. Example: `cmake -DWITH_NOISE_DEBUG=ON ..`
3. Change `syspath` variable to the cloned lattice-estimator directory path in binfhe_params_helper.py
4. Set `export OMP_NUM_THREADS =1` (this is optional if you need single threaded runtimes for keygen and evalbingate while choosing optimal parameters)
5. Run the script binfhe_params.py with sage as `sage -python scripts/paramsestimator/binfhe_params.py` and answer the prompts (the optimal parameters differ based on the input keyswitching digit size d_ks). If you are not sure about a prompt, skip to use the default values.
