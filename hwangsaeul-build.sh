#!/bin/sh

meson build \
        -Dcustom_subprojects=chamge,x264 \
        -Dintrospection=disabled \
        -Ddoc=disabled \
        -Dexamples=disabled \
        -Dges=disabled
