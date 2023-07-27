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
Enter expected decryption failure rate (for example, enter -32 for 2^-32 failure rate)[default = -32]: -37
Enter expected number of inputs to the boolean gate [default = 2]: 
Enter expected number of samples to estimate noise [default = 150]: 
Enter key switching digit size [default = 2, 3, or 4]: 3
Enter number of threads that can be used to run the lattice-estimator (only used for the estimator): 8
Input parameters: 
bootstrapping_tech:  2
dist_type:  ternary
sec_level:  STD128
expected decryption failure rate:  -37
num_of_inputs:  2
num_of_samples:  150
d_ks:  2
num_of_threads:  8
...
```
> sample output:
```
d_g loop:  2
final parameters
dist_type:  ternary
bootstrapping_tech:  2
sec_level:  STD128
expected decryption failure rate:  -37
actual decryption failure rate:  -72.25964982938888
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
Performance:  {'BootstrapKeyGenTime': ' 1071 milliseconds\n', 'BootstrappingKeySize': ' 68065309\n bytes', 'KeySwitchingKeySize': ' 820543525\n bytes', 'CiphertextSize': ' 4189\n bytes', 'EvalBinGateTime': ' 197 milliseconds\n'}
commandline arguments:  -n 518 -q 2048 -N 2048 -Q 54.0 -k 16384.0 -g 134217728 -b 32 -t 2 -d 1 -r 32 -s 3.19 -i 1000
...
d_g loop:  3
final parameters
dist_type:  ternary
bootstrapping_tech:  2
sec_level:  STD128
expected decryption failure rate:  -37
actual decryption failure rate:  -62.2296386723353
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
Performance:  {'BootstrapKeyGenTime': ' 559 milliseconds\n', 'BootstrappingKeySize': ' 68218637\n bytes', 'KeySwitchingKeySize': ' 410271781\n bytes', 'CiphertextSize': ' 4189\n bytes', 'EvalBinGateTime': ' 112 milliseconds\n'}
commandline arguments:  -n 518 -q 1024 -N 1024 -Q 27.0 -k 16384.0 -g 512 -b 32 -t 2 -d 1 -r 32 -s 3.19 -i 1000
...
d_g loop:  4
final parameters
dist_type:  ternary
bootstrapping_tech:  2
sec_level:  STD128
expected decryption failure rate:  -37
actual decryption failure rate:  -34.60273103257841
num_of_inputs:  2
num_of_samples:  150
Output parameters: 
lattice dimension n:  516
ringsize N:  1024
lattice modulus n:  1024
size of ring modulus Q:  27.0
optimal key switching modulus  Qks:  8192.0
gadget digit base B_g:  128
key switching digit base B_ks:  32
key switching digit size B_ks:  3
Performance:  {'BootstrapKeyGenTime': ' 574 milliseconds\n', 'BootstrappingKeySize': ' 101924557\n bytes', 'KeySwitchingKeySize': ' 408698917\n bytes', 'CiphertextSize': ' 4173\n bytes', 'EvalBinGateTime': ' 178 milliseconds\n'}
commandline arguments:  -n 516 -q 1024 -N 1024 -Q 27.0 -k 8192.0 -g 128 -b 32 -t 2 -d 1 -r 32 -s 3.19 -i 1000
```
Note that the output is provided for digit sizes 2, 3, 4 as indicated by the line `d_g loop: ` in the sample. The digit size influences the performance runtime and the noise level (which consequently affects the decryption failure rate). From the output parameters for different digit sizes, you can choose the one with the best performance. In the above sample output, this would be for digit size 3. The performance with 32 bits native word size is always better than 64 bits and we upper bound the size of the modulus to 28 bits to use 32 bit native word. The input parameter key switching digit size d_ks influences the bootstrapping keygen time (in generating the internal key switching key), key switching key size and the noise level. A higher value of d_ks results in faster keygen time and smaller key size but larger noise. The commandline arguments printed out by the script can be run with the `boolean_estimate_time.cpp` executable to get more accurate runtimes.