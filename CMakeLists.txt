cmake_minimum_required(VERSION 3.22)

# Setup compiler settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)

# Define the build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()

# Set the project name
set(CMAKE_PROJECT_NAME profiler_demo)

# Enable compile command to ease indexing with e.g. clangd
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

# Core project settings
project(${CMAKE_PROJECT_NAME})
message("Build type: " ${CMAKE_BUILD_TYPE})

option(PROFILER_APP_INSTRUMENTED "Instrument selected application files" OFF)
option(PROFILER_APP_SCOPES "Measure selected call sites with explicit profiler scopes (no instrumentation)" OFF)
if(PROFILER_APP_INSTRUMENTED AND PROFILER_APP_SCOPES)
    message(FATAL_ERROR "PROFILER_APP_INSTRUMENTED and PROFILER_APP_SCOPES are mutually exclusive")
endif()

# Enable CMake support for ASM and C languages
enable_language(C ASM)

# Create an executable object type
add_executable(${CMAKE_PROJECT_NAME})

# Add STM32CubeMX generated sources
add_subdirectory(cmake/stm32cubemx)

# Configure paths to the shared profiler repository
set(PROFILER_DEFAULT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/modules/profiler")
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../profiler/CMakeLists.txt")
    set(PROFILER_DEFAULT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../profiler")
endif()
set(STREAM_DEFAULT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/modules/stream")
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../stream/stream.c")
    set(STREAM_DEFAULT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../stream")
endif()
set(PROFILER_DIR "${PROFILER_DEFAULT_DIR}" CACHE PATH "Path to the profiler module")
set(STREAM_DIR "${STREAM_DEFAULT_DIR}" CACHE PATH "Path to the stream module")
set(HASHMAP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../hashmap" CACHE PATH "Path to the hashmap module")
set(PROFILER_PORT "16bit_it" CACHE STRING "Profiler port backend")
set(PROFILER_TRANSPORT "uart_stm32_hal_dma" CACHE STRING "Profiler transport backend")

if(NOT EXISTS "${PROFILER_DIR}/profiler.c")
    message(FATAL_ERROR "Profiler module not found at '${PROFILER_DIR}'.")
endif()

add_subdirectory("${PROFILER_DIR}" profiler_build)
target_link_libraries(profiler PRIVATE stm32cubemx)

# Add sources to executable
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/profiler_port.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/benchmark/Src/benchmark_exact.s"
    "${CMAKE_CURRENT_SOURCE_DIR}/benchmark/Src/benchmark_algo.c"
)

# Add include paths
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/Core/Inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/benchmark/Inc"
)

# Add project symbols (macros)
if(PROFILER_PORT STREQUAL "16bit_it")
    target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE PROFILER_PORT_USE_16BIT_IT)
endif()

# Remove wrong libob.a library dependency when using cpp files
list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)

# Add linked libraries
target_link_libraries(${CMAKE_PROJECT_NAME}
    -Wl,--whole-archive profiler -Wl,--no-whole-archive
    stm32cubemx
)

# Profiler duration semantics: ACTIVE (CPU time only, default) or WALL
set(PROFILER_APP_TIME_MODE "ACTIVE" CACHE STRING "Profiler time mode (WALL or ACTIVE)")
set_property(CACHE PROFILER_APP_TIME_MODE PROPERTY STRINGS WALL ACTIVE)
if(PROFILER_APP_TIME_MODE STREQUAL "ACTIVE")
    target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE PROFILER_APP_TIME_MODE=1)
elseif(PROFILER_APP_TIME_MODE STREQUAL "WALL")
    target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE PROFILER_APP_TIME_MODE=0)
else()
    message(FATAL_ERROR "PROFILER_APP_TIME_MODE must be WALL or ACTIVE")
endif()

# Explicit call-site scopes: uninstrumented code, same binary as the WCET analysis.
if(PROFILER_APP_SCOPES)
    target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE PROFILER_SCOPES_ENABLED)
endif()

# Instrument application files for function call tracing.
if(PROFILER_APP_INSTRUMENTED)
    target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE PROFILER_APP_INSTRUMENTED)
    set_source_files_properties(
        "${CMAKE_CURRENT_SOURCE_DIR}/benchmark/Src/benchmark_algo.c"
        PROPERTIES COMPILE_OPTIONS "-finstrument-functions"
    )
endif()

# Keep profiler callbacks out of instrumentation
set_source_files_properties(
    "${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/profiler_port.c"
    PROPERTIES COMPILE_OPTIONS "-fno-instrument-functions"
)
