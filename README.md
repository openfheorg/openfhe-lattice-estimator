Instructions to run the script:

Pre-requisites: lattice-estimator, python and sage installed in the machine (can run in the icelake machine)

1. Set BINFHE_DEBUG flag in openfhe
2. compile openfhe code in its repository (with directory name build32 for 32 bit word size and build64 for 64 bit word size)
3. change syspath variable to this openfhe directory path in binfhe_params_helper.py
4. set export OMP_NUM_THREADS =1 (optional)
5. run the script binfhe_params.py with sage from the lattice-estimator folder as sage -python scripts/paramsestimator/binfhe_params.py and answer the prompts (the optimal parameters differ based on the input keyswitching digit size d_ks)
