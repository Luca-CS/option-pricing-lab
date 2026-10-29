cmake_minimum_required(VERSION 3.24)
project(optlab_cuda LANGUAGES CXX CUDA)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_ARCHITECTURES 75)
find_package(CUDAToolkit REQUIRED)          # CUDA::cudart_static; CUDA::curand if the host API is ever needed

add_library(optlab_cuda STATIC src/cpu_mc.cpp src/gpu_mc.cu src/closed_forms.cpp)
target_include_directories(optlab_cuda PUBLIC include)
target_compile_options(optlab_cuda PRIVATE $<$<COMPILE_LANGUAGE:CUDA>:--ptxas-options=-v -lineinfo>)
target_link_libraries(optlab_cuda PUBLIC CUDA::cudart_static)
if(MSVC)  # cudart_static pulls LIBCMT while the targets use the /MD runtime
  target_link_options(optlab_cuda INTERFACE /NODEFAULTLIB:LIBCMT)
endif()

add_executable(test_prices tests/test_prices.cpp)
target_link_libraries(test_prices optlab_cuda)
add_executable(bench bench/bench.cpp)
target_link_libraries(bench optlab_cuda)

enable_testing()
add_test(NAME prices COMMAND test_prices)
add_test(NAME qe_bias COMMAND test_prices qe-bias)
