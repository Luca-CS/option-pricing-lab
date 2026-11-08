# optlab.native's compiled module, built by scikit-build-core (pyproject.toml): the engine of cuda/ (CUDA when a toolkit
# is found) and its nanobind binding, optlab.native._ext. The engine's own tests and benchmarks build from cuda/ alone
# (cmake -S packages/native/cuda -B build); here they are left out.
cmake_minimum_required(VERSION 3.24)
project(optlab_native LANGUAGES CXX)

set(OPTLAB_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
add_subdirectory(cuda)

find_package(Python 3.14 COMPONENTS Interpreter Development.Module REQUIRED)
execute_process(COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir OUTPUT_STRIP_TRAILING_WHITESPACE
                OUTPUT_VARIABLE nanobind_ROOT)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_ext NB_STATIC bindings/ext.cpp)
target_link_libraries(_ext PRIVATE optlab_engine)
install(TARGETS _ext LIBRARY DESTINATION optlab/native)
