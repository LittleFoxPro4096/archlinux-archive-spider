#!/bin/bash

# If a proxy is needed, uncomment the following two lines and adjust the port
#export http_proxy="http://127.0.0.1:20171"
#export https_proxy="http://127.0.0.1:20171"

install -dm755 archive/{core,extra,multilib} log

for repo in core multilib extra; do
    aria2c --dir="archive/$repo"              \
           --input-file="manifest/$repo.txt"  \
           --save-session="log/$repo.session" \
           --save-session-interval=10         \
           --continue=true                    \
           --conditional-get=true             \
           --max-concurrent-downloads=5       \
           --max-connection-per-server=16     \
           --min-split-size=1M                \
           --split=4                          \
           --max-tries=10                     \
           --retry-wait=3                     \
           --timeout=60                       \
           --connect-timeout=30               \
           --summary-interval=30              \
           --show-console-readout=false       \
           --auto-file-renaming=false         \
           --allow-overwrite=false            \
           --file-allocation=none             \
           | tee "log/$repo.log"
done
