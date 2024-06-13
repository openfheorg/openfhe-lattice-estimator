#!/bin/bash

param_set=${1}
boot_tech=${2}
num_input=${3}
num_iters=${4}
path=${5}

echo $path
if [ -n "$param_set" ] && [ -n "$boot_tech" ] && [ -n "$num_input" ] && [ -n "$num_iters" ]
then
  $path/bin/boolean_noise_estimate_script -p $param_set -t $boot_tech -I $num_input -i $num_iters
else
  echo "argument missing"
fi
