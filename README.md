Instructions to run the script:

Pre-requisites: 

1. git clone the lattice-estimator repository (`git clone https://github.com/malb/lattice-estimator.git`)
2. Install python (tested with 3.8.10)
3. Install sage (tested with SageMath version 9.0)

Steps to run the script:
1. compile openfhe code in its repository with the WITH_NOISE_DEBUG flag set to ON
`cmake -DWITH_NOISE_DEBUG=ON ..`
2. change syspath variable to the cloned lattice-estimator directory path in binfhe_params_helper.py
3. set export OMP_NUM_THREADS =1 (this is optional and would result in single threaded runtimes for keygen and evalbingate for choosing parameters)
4. run the script binfhe_params.py with sage as sage -python scripts/paramsestimator/binfhe_params.py and answer the prompts (the optimal parameters differ based on the input keyswitching digit size d_ks). If you are not sure about a prompt, skip to use the default values.
