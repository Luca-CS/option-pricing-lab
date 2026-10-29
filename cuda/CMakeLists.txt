cmake_minimum_required(VERSION 3.24)
project(optlab_cuda LANGUAGES CXX CUDA)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_ARCHITECTURES 75)
find_package(CUDAToolkit REQUIRED)          # CUDA::cudart_static; CUDA::curand if the host API is ever needed

add_library(optlab_cuda STATIC src/cpu_mc.cpp src/gpu_mc.cu src/closed_forms.cpp)
target_include_directories(optlab_cuda PUBLIC include)
# Approximate single-precision division and square root, flush to zero: 1.8x on the Heston QE kernel for a price
# change of 0.001 standard error (bench/ablation.cpp). exp and log stay precise: their fast versions (__expf,
# __logf) bias the Heston barrier by 0.13 standard error. Double precision is not affected by these flags.
set(OPTLAB_CUDA_MATH -prec-div=false -prec-sqrt=false -ftz=true)
target_compile_options(optlab_cuda PRIVATE
  $<$<COMPILE_LANGUAGE:CUDA>:--ptxas-options=-v -lineinfo ${OPTLAB_CUDA_MATH}>)
target_link_libraries(optlab_cuda PUBLIC CUDA::cudart_static)
if(MSVC)  # cudart_static pulls LIBCMT while the targets use the /MD runtime
  target_link_options(optlab_cuda INTERFACE /NODEFAULTLIB:LIBCMT)
endif()

add_executable(test_prices tests/test_prices.cpp)
target_link_libraries(test_prices optlab_cuda)
add_executable(bench bench/bench.cpp)
target_link_libraries(bench optlab_cuda)

# Optimisation study: the same library compiled a second time with --use_fast_math, for one row of the ablation.
add_library(optlab_cuda_fastmath STATIC src/cpu_mc.cpp src/gpu_mc.cu src/closed_forms.cpp)
target_include_directories(optlab_cuda_fastmath PUBLIC include)
target_compile_options(optlab_cuda_fastmath PRIVATE $<$<COMPILE_LANGUAGE:CUDA>:--use_fast_math -lineinfo>)
target_link_libraries(optlab_cuda_fastmath PUBLIC CUDA::cudart_static)
if(MSVC)
  target_link_options(optlab_cuda_fastmath INTERFACE /NODEFAULTLIB:LIBCMT)
endif()
add_executable(ablation bench/ablation.cpp)
target_link_libraries(ablation optlab_cuda)
add_executable(ablation_fastmath bench/ablation.cpp)
target_link_libraries(ablation_fastmath optlab_cuda_fastmath)
target_compile_definitions(ablation_fastmath PRIVATE OPTLAB_BUILD_KIND=1)

# nvcc's default IEEE division and square root, for the row the production flags are compared against.
add_library(optlab_cuda_ieee STATIC src/cpu_mc.cpp src/gpu_mc.cu src/closed_forms.cpp)
target_include_directories(optlab_cuda_ieee PUBLIC include)
target_compile_options(optlab_cuda_ieee PRIVATE $<$<COMPILE_LANGUAGE:CUDA>:-lineinfo>)
target_link_libraries(optlab_cuda_ieee PUBLIC CUDA::cudart_static)
if(MSVC)
  target_link_options(optlab_cuda_ieee INTERFACE /NODEFAULTLIB:LIBCMT)
endif()
add_executable(ablation_ieee bench/ablation.cpp)
target_link_libraries(ablation_ieee optlab_cuda_ieee)
target_compile_definitions(ablation_ieee PRIVATE OPTLAB_BUILD_KIND=2)

enable_testing()
add_test(NAME prices COMMAND test_prices)
add_test(NAME qe_bias COMMAND test_prices qe-bias)
