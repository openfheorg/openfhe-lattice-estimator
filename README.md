OpenFHE Lattice Estimator
=====================================

A parameter generation tool for DM, CGGI, and LMKCDEY. The tool takes as user input the desired design (e.g., bootstrapping method, failure probability, security level, etc.) and outputs several sets of optimized parameters (e.g., lattice parameter, ciphertext modulus, digit size), along with the runtime of bootstrapping for each set of parameters.

## Pre-requisites: 

1. Install python with `sudo apt install python3` (tested with 3.8.10).
2. Install sage with `sudo apt install sagemath` (tested with SageMath version 9.0).
3. Install `numpy` and `scipy` using `pip3 install`.
4. Clone the lattice-estimator repository (`git clone https://github.com/malb/lattice-estimator.git`).
5. Add path for the cloned lattice-estimator directory to your PYTHONPATH environment variable.
6. Clone the [openfhe-development](https://github.com/openfheorg/openfhe-development) repository and checkout the `dev` branch.
7. Follow the `Installation` instructions of the openfhe-development README to build and install the library. NOTE: The `WITH_NOISE_DEBUG` flag must be set to `ON` while running cmake (e.g., `cmake -DWITH_NOISE_DEBUG=ON ..`) for propper integration with openfhe-lattice-estimator scripts.

## Installation

1. Clone the [openfhe-lattice-estimator](https://github.com/openfheorg/openfhe-lattice-estimator) repository.
2. Change to the openfhe-lattice-estimator directory and run
   ```
   mkdir build
   cd build
   cmake ..
   make
   ```
3. Optionally, if single core runtimes for KeyGen or EvalBinGate are needed while generating optimal parameters, set OMP_NUM_THREADS to 1 (e.g., `export OMP_NUM_THREADS=1`)

## Instructions for binfhe_params.py

From the openfhe-lattice-estimator directory, execute `sage -python scripts/paramsestimator/binfhe_params.py` and answer the `Parameter Selector` prompts (press ENTER to select the default values).

Below is an example run for a set of input parameters and the corresponding output parameters from the script:

> sample input:
```
Parameter selector for FHEW like schemes
Enter Bootstrapping technique (1 = AP, 2 = GINX, 3 = LMKCDEY) [default = 2]:
Enter Secret distribution (0 = error, 1 = ternary) [default = 1]:
Enter Security level (STD128, STD128Q, STD192, STD192Q, STD256, STD256Q) [default = STD128]:
Enter expected decryption failure rate (for example, enter -32 for 2^-32 failure rate) [default = -40]: -37
Enter expected number of inputs to the boolean gate (2, 3, or 4) [default = 2]:
Enter expected number of samples to estimate noise [default = 200]: 150
Enter key switching digit size (2, 3, or 4) [default = 3]:
Enter number of threads that can be used to run the lattice-estimator (only used for the estimator) [default = 1]: 8
Input parameters:
bootstrapping_tech:  2
dist_type:  ternary
sec_level:  STD128
expected decryption failure rate:  -37
num_of_inputs:  2
num_of_samples:  150
d_ks:  3
num_of_threads:  8
...
```

> sample output:
```
d_g loop:  2
...
final parameters
dist_type:  ternary
bootstrapping_tech:  2
sec_level:  STD128
expected decryption failure rate:  -37
actual decryption failure rate:  -50.26382359792761
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
Performance:  {'BootstrapKeyGenTime': ' 3995 milliseconds\n', 'BootstrappingKeySize': ' 68065309\n bytes', 'KeySwitchingKeySize': ' 820543525\n bytes', 'CiphertextSize': ' 4189\n bytes', 'EvalBinGateTime': ' 126 milliseconds\n'}
commandline arguments:  -n 518 -N 2048 -q 2048 -Q 54 -k 16384 -g 134217728 -r 32 -b 32 -s 3.19 -t 2 -d 1 -I 2 -i 150
table entry:  54, 4096, 518, 2048, 16384, 3.19, 32, 134217728, 32, 10, UNIFORM_TERNARY

d_g loop:  3
...
final parameters
dist_type:  ternary
bootstrapping_tech:  2
sec_level:  STD128
expected decryption failure rate:  -37
actual decryption failure rate:  -42.809810473714904
num_of_inputs:  2
num_of_samples:  150
Output parameters:
lattice dimension n:  518
ringsize N:  1024
lattice modulus n:  1024
size of ring modulus Q:  27.0
optimal key switching modulus  Qks:  16384.0
gadget digit base B_g:  512
key switching digit base B_ks:  32
key switching digit size B_ks:  3
Performance:  {'BootstrapKeyGenTime': ' 2195 milliseconds\n', 'BootstrappingKeySize': ' 68218637\n bytes', 'KeySwitchingKeySize': ' 410271781\n bytes', 'CiphertextSize': ' 4189\n bytes', 'EvalBinGateTime': ' 94 milliseconds\n'}
commandline arguments:  -n 518 -N 1024 -q 1024 -Q 27 -k 16384 -g 512 -r 32 -b 32 -s 3.19 -t 2 -d 1 -I 2 -i 150
table entry:  27, 2048, 518, 1024, 16384, 3.19, 32, 512, 32, 10, UNIFORM_TERNARY

d_g loop:  4
...
final parameters
dist_type:  ternary
bootstrapping_tech:  2
sec_level:  STD128
expected decryption failure rate:  -37
actual decryption failure rate:  -70.6689426176026
num_of_inputs:  2
num_of_samples:  150
Output parameters:
lattice dimension n:  518
ringsize N:  1024
lattice modulus n:  1024
size of ring modulus Q:  27.0
optimal key switching modulus  Qks:  16384.0
gadget digit base B_g:  128
key switching digit base B_ks:  32
key switching digit size B_ks:  3
Performance:  {'BootstrapKeyGenTime': ' 2420 milliseconds\n', 'BootstrappingKeySize': ' 102319613\n bytes', 'KeySwitchingKeySize': ' 410271781\n bytes', 'CiphertextSize': ' 4189\n bytes', 'EvalBinGateTime': ' 128 milliseconds\n'}
commandline arguments:  -n 518 -N 1024 -q 1024 -Q 27 -k 16384 -g 128 -r 32 -b 32 -s 3.19 -t 2 -d 1 -I 2 -i 150
table entry:  27, 2048, 518, 1024, 16384, 3.19, 32, 128, 32, 10, UNIFORM_TERNARY

out_file_ Cleanup completed.
noise_file_ Cleanup completed.
```

Note that parameters are generated for digit sizes 2, 3 and 4 as indicated by the `d_g loop:` lines in the sample output. One should choose output parameters for the digit size that provides the best performance (e.g., digit size 3 for the sample output from above).
- Digit size influences runtime performance and noise level, which consequently affects the decryption failure rate. 
- The performance for 32-bit native word size is always better than for 64-bit word size; we restrict the maximum size of the modulus to 28 bits to use 32-bit native word size (28 bits is the maximum modulus size supported for 32-bit words in OpenFHE).
- The input parameter key-switching digit size d_ks influences the bootstrapping keygen time (in generating the internal key switching key), key switching key size and the noise level. A higher value of d_ks results in faster keygen time and smaller key size but larger noise.

To bypass the `Parameter Selector` prompts, execute binfhe_params.py with any of {t, d, p, f, I, i, k, n} arguments e.g.,
```
sage -python scripts/paramsestimator/binfhe_params.py -t 2 -d 1 -p STD128 -f -37 -I 2 -i 150 -k 3 -n 8
```
or
```
sage -python scripts/paramsestimator/binfhe_params.py -f -37 -i 150 -n 8
```
to select the same set of input parameters used in the example run from above.

To iterate through all valid combinations of {p, I, d} arguments for a given t argument, execute binfhe_params.py with a -t argument and the --all flag e.g.,
```
sage -python scripts/paramsestimator/binfhe_params.py -t 3 --all
```
for LMKCDEY.
 
## Instructions for binfhe_params_validator.py

To calculate noise std deviation and probability of failure for a specific named BINFHE_PARAMSET within OpenFHE, execute binfhe_params_validator.py with the appropriate set of {p, t, I, i} arguments e.g.,
```
python3 scripts/paramsestimator/binfhe_params_validator.py -p STD128_4_LMKCDEY -t 3 -I 4 -i 1000
```
for 1000 iterations of 4-input LMKCDEY at STD128.

To calculate noise std deviation and probability of failure for all named BINFHE_PARAMSET within OpenFHE, execute binfhe_params_validator.py with -p ALL e.g.,
```
python3 scripts/paramsestimator/binfhe_params_validator.py -p ALL -i 1000
```

To calculate noise std deviation and probability of failure for the `commandline arguments` printed out by the binfhe_params.py script, execute binfhe_params_validator.py without a -p argument and with the `commandline arguments` output e.g.,
```
python3 scripts/paramsestimator/binfhe_params_validator.py -n 518 -N 1024 -q 1024 -Q 27 -k 16384 -g 512 -r 32 -b 32 -s 3.19 -t 2 -d 1 -I 2 -i 150
```
for the `d_g loop: 3` parameters from above.
