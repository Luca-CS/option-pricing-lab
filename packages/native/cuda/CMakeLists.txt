cmake_minimum_required(VERSION 3.24)
project(optlab_cuda LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)

# The CPU engines build anywhere with a C++17 compiler; CUDA is added when a toolkit is found (OPTLAB_WITH_CUDA=OFF
# forces a CPU-only build). GPU code: OPTLAB_CUDA_ARCHS=native compiles for the GPUs of this machine; "portable"
# for Pascal to Blackwell consumer cards (GTX 10xx to RTX 50xx) plus PTX that newer drivers compile at load time.
option(OPTLAB_WITH_CUDA "Build the CUDA backend when a CUDA toolkit is found" ON)
option(OPTLAB_CPU_AVX2 "Compile the CPU engines for AVX2 (every x86-64 CPU since 2013)" ON)
set(OPTLAB_CUDA_ARCHS "native" CACHE STRING "CUDA architectures: native, portable, or a CMake list such as 75;86")
option(OPTLAB_BUILD_TOOLS "Build the C++ tests, benchmarks and the ablation study (off inside the Python wheel)" ON)

include(CheckLanguage)
if(OPTLAB_WITH_CUDA)
  check_language(CUDA)
endif()
if(OPTLAB_WITH_CUDA AND CMAKE_CUDA_COMPILER)
  if(OPTLAB_CUDA_ARCHS STREQUAL "portable")
    set(CMAKE_CUDA_ARCHITECTURES 61-real 75-real 86-real 89-real 120-real 120-virtual)
  else()
    set(CMAKE_CUDA_ARCHITECTURES ${OPTLAB_CUDA_ARCHS})
  endif()
  enable_language(CUDA)
  set(CMAKE_CUDA_STANDARD 17)
  find_package(CUDAToolkit REQUIRED)
  set(OPTLAB_HAVE_CUDA ON)
  message(STATUS "optlab: CUDA backend on, architectures ${CMAKE_CUDA_ARCHITECTURES}")
else()
  set(OPTLAB_HAVE_CUDA OFF)
  message(STATUS "optlab: no CUDA toolkit, CPU engines only")
endif()

# CPU engines: the double reference of the fixed products and the path engine of the Python library.
add_library(optlab_cpu STATIC src/cpu_mc.cpp src/closed_forms.cpp src/paths_cpu.cpp src/leverage.cpp)
target_include_directories(optlab_cpu PUBLIC include)
set_target_properties(optlab_cpu PROPERTIES POSITION_INDEPENDENT_CODE ON)
find_package(Threads REQUIRED)
target_link_libraries(optlab_cpu PUBLIC Threads::Threads)
if(OPTLAB_CPU_AVX2 AND CMAKE_SYSTEM_PROCESSOR MATCHES "AMD64|x86_64|x86|i686")
  if(MSVC)
    target_compile_options(optlab_cpu PRIVATE /arch:AVX2)
  else()
    target_compile_options(optlab_cpu PRIVATE -mavx2 -mfma)
  endif()
endif()

# Approximate single-precision division and square root, flush to zero: 1.8x on the Heston QE kernel for a price
# change of 0.001 standard error (bench/ablation.cpp). exp and log stay precise: their fast versions (__expf,
# __logf) bias the Heston barrier by 0.13 standard error. Double precision is not affected by these flags.
set(OPTLAB_CUDA_MATH -prec-div=false -prec-sqrt=false -ftz=true)

if(OPTLAB_HAVE_CUDA)
  add_library(optlab_cuda STATIC src/gpu_mc.cu src/paths_gpu.cu src/leverage_gpu.cu)
  target_include_directories(optlab_cuda PUBLIC include)
  target_compile_options(optlab_cuda PRIVATE
    $<$<COMPILE_LANGUAGE:CUDA>:--ptxas-options=-v -lineinfo ${OPTLAB_CUDA_MATH}>)
  target_link_libraries(optlab_cuda PUBLIC optlab_cpu CUDA::cudart_static)
  target_compile_definitions(optlab_cuda PUBLIC OPTLAB_HAVE_CUDA)
  if(MSVC)  # cudart_static pulls LIBCMT while the targets use the /MD runtime
    target_link_options(optlab_cuda INTERFACE /NODEFAULTLIB:LIBCMT)
  endif()
  set_target_properties(optlab_cuda PROPERTIES POSITION_INDEPENDENT_CODE ON)  # linked into the shared library
endif()

# The checked requests (requests.hpp) over the engines: what the Python module (optlab.native._ext, built by
# packages/native/CMakeLists.txt with nanobind) and the C++ tests call. Static, with the CUDA runtime linked
# statically when present, so the module is self-contained.
add_library(optlab_engine STATIC src/requests.cpp)
target_include_directories(optlab_engine PUBLIC include)
set_target_properties(optlab_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(OPTLAB_HAVE_CUDA)
  target_link_libraries(optlab_engine PUBLIC optlab_cuda)
else()
  target_link_libraries(optlab_engine PUBLIC optlab_cpu)
endif()

if(NOT OPTLAB_BUILD_TOOLS)
  return()
endif()

add_executable(test_paths tests/test_paths.cpp)
target_link_libraries(test_paths PRIVATE optlab_engine)  # through the checked requests, as Python calls them
enable_testing()
add_test(NAME paths COMMAND test_paths)

if(OPTLAB_HAVE_CUDA)
  add_executable(test_prices tests/test_prices.cpp)
  target_link_libraries(test_prices optlab_cuda)
  add_executable(bench bench/bench.cpp)
  target_link_libraries(bench optlab_cuda)
  add_executable(profile bench/profile.cpp)  # Nsight Compute target
  target_link_libraries(profile optlab_cuda)

  # Optimisation study: the same library compiled a second time with --use_fast_math, for one row of the ablation.
  add_library(optlab_cuda_fastmath STATIC src/gpu_mc.cu)
  target_include_directories(optlab_cuda_fastmath PUBLIC include)
  target_compile_options(optlab_cuda_fastmath PRIVATE $<$<COMPILE_LANGUAGE:CUDA>:--use_fast_math -lineinfo>)
  target_link_libraries(optlab_cuda_fastmath PUBLIC optlab_cpu CUDA::cudart_static)
  if(MSVC)
    target_link_options(optlab_cuda_fastmath INTERFACE /NODEFAULTLIB:LIBCMT)
  endif()
  add_executable(ablation bench/ablation.cpp)
  target_link_libraries(ablation optlab_cuda)
  add_executable(ablation_fastmath bench/ablation.cpp)
  target_link_libraries(ablation_fastmath optlab_cuda_fastmath)
  target_compile_definitions(ablation_fastmath PRIVATE OPTLAB_BUILD_KIND=1)

  # nvcc's default IEEE division and square root, for the row the production flags are compared against.
  add_library(optlab_cuda_ieee STATIC src/gpu_mc.cu)
  target_include_directories(optlab_cuda_ieee PUBLIC include)
  target_compile_options(optlab_cuda_ieee PRIVATE $<$<COMPILE_LANGUAGE:CUDA>:-lineinfo>)
  target_link_libraries(optlab_cuda_ieee PUBLIC optlab_cpu CUDA::cudart_static)
  if(MSVC)
    target_link_options(optlab_cuda_ieee INTERFACE /NODEFAULTLIB:LIBCMT)
  endif()
  add_executable(ablation_ieee bench/ablation.cpp)
  target_link_libraries(ablation_ieee optlab_cuda_ieee)
  target_compile_definitions(ablation_ieee PRIVATE OPTLAB_BUILD_KIND=2)

  add_test(NAME prices COMMAND test_prices)
  add_test(NAME qe_bias COMMAND test_prices qe-bias)
endif()
