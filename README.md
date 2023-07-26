Instructions to run the script:

Pre-requisites: 

1. git clone the lattice-estimator repository (`git clone https://github.com/malb/lattice-estimator.git`)
2. Install python with `sudo apt install python3` (tested with 3.8.10)
3. Install sage with `sudo apt install sagemath` (tested with SageMath version 9.0)

Steps to run the script:
1. Clone the [openfhe-development](https://github.com/openfheorg/openfhe-development) repository and checkout the `dev` branch.
2. Follow the `Installation` section of the OpenFHE readme to build and install the library. Use the flag `WITH_NOISE_DEBUG` set to `ON` while running cmake. Example: `cmake -DWITH_NOISE_DEBUG=ON ..`
3. Clone the [openfhe-lattice-estimator](https://github.com/openfheorg/openfhe-lattice-estimator) repository.
4. Change to the local directory and run
   ```
   mkdir build
   cd build
   cmake ..
   make
   ```
6. Change `syspath` variable to the cloned lattice-estimator directory path in binfhe_params_helper.py
7. Set `export OMP_NUM_THREADS=1` (this is optional if you need single threaded runtimes for keygen and evalbingate while choosing optimal parameters)
8. Run the script binfhe_params.py with sage as `sage -python scripts/paramsestimator/binfhe_params.py` and answer the prompts (the optimal parameters differ based on the input keyswitching digit size d_ks). If you are not sure about a prompt, skip to use the default values.

Below is an example run for a set of input parameters and the corresponding output parameters from the script:

> sample input:
```
Parameter selector for FHEW like schemes
Enter Bootstrapping technique (1 = AP, 2 = GINX, 3 = LMKCDEY): 2
Enter Secret distribution (0 = error, 1 = ternary): 1
Enter Security level (STD128, STD128Q, STD192, STD192Q, STD256, STD256Q) [default = STD128]: 
Enter expected decryption failure rate (for example, enter -32 for 2^-32 failure rate)[default = -32]: -35
Enter expected number of inputs to the boolean gate [default = 2]: 
Enter expected number of samples to estimate noise [default = 150]: 
Enter key switching digit size [default = 2, 3, or 4]: 
Enter number of threads that can be used to run the lattice-estimator (only used for the estimator): 8
Input parameters: 
bootstrapping_tech:  2
dist_type:  ternary
sec_level:  STD128
expected decryption failure rate:  -35
num_of_inputs:  2
num_of_samples:  150
d_ks:  2
num_of_threads:  8
d_g loop:  2
Target noise for this iteration:  13.608357619852871
scripts/run_script.sh 400 1024 1024 27.0 1024.0 16384 32 32 3.19 150 1 2 build > out_file_404 2>noise_file_404
...
```
> sample output:
```
final parameters
dist_type:  ternary
bootstrapping_tech:  2
sec_level:  STD128
expected decryption failure rate:  -35
actual decryption failure rate:  -84.32982216679146
num_of_inputs:  2
num_of_samples:  150
Output parameters: 
lattice dimension n:  518
ringsize N:  2048
lattice modulus n:  2048
size of ring modulus Q:  54.0
optimal key switching modulus  Qks:  16384.0
gadget digit base B_g:  134217728
key switching digit base B_ks:  32
key switching digit size B_ks:  3
Performance:  {'BootstrapKeyGenTime': ' 1098 milliseconds\n', 'BootstrappingKeySize': ' 68065309\n bytes', 'KeySwitchingKeySize': ' 820543525\n bytes', 'CiphertextSize': ' 4189\n bytes', 'EvalBinGateTime': ' 188 milliseconds\n'}
commandline arguments:  -n 518 -q 2048 -N 2048 -Q 54.0 -k 16384.0 -g 134217728 -b 32 -t 2 -d 1 -r 32 -s 3.19 -i 1000
```
The commandline arguments printed out by the script can be run with the `boolean_estimate_time.cpp` executable to get more accurate runtimes.