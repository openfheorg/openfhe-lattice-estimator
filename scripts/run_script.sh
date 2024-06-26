#!/bin/bash

dim_n=$1
mod_q=$2
dim_N=$3
mod_logQ=$4
mod_Qks=$5
B_g=$6
B_ks=$7
B_rk=$8
sigma=$9
num_samples=${10}
secret_dist=${11}
bootstrapping_tech=${12}
num_inputs=${13}
path=${14}

echo $path
if [ -n "$dim_n" ] && [ -n "$mod_q" ] && [ -n "$dim_N" ] && [ -n "$mod_logQ" ] && [ -n "$mod_Qks" ] && [ -n "$B_g" ] && [ -n "$B_ks" ] && [ -n "$B_rk" ] && [ -n "$sigma" ]
then
  $path/bin/boolean_noise_estimate_script -n $dim_n -q $mod_q -N $dim_N -Q $mod_logQ -k $mod_Qks -g $B_g -b $B_ks -r $B_rk -s $sigma -t $bootstrapping_tech -d $secret_dist -i $num_samples -I $num_inputs
else
  echo "argument missing"
fi
