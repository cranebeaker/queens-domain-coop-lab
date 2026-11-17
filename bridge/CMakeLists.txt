cmake_minimum_required(VERSION 3.25)
project(qdbridge LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")

set(QD_SDK ${CMAKE_CURRENT_SOURCE_DIR}/../sdk/CppSDK CACHE PATH "Dumper-7 C++ SDK of the game")
set(COOPBRIDGE ${CMAKE_CURRENT_SOURCE_DIR}/../third_party/coopbridge/include CACHE PATH "coopbridge headers (from the Co-op Anything hub, MIT)")

add_compile_options(/utf-8 /MP /bigobj /permissive- /W3 /wd4244 /wd4267 /wd4018 /wd4005 /EHsc)
add_compile_definitions(WIN32_LEAN_AND_MEAN NOMINMAX _CRT_SECURE_NO_WARNINGS)

add_library(qdbridge SHARED
    qdbridge.cpp
    ${QD_SDK}/SDK/Basic.cpp
    ${QD_SDK}/SDK/CoreUObject_functions.cpp
    ${QD_SDK}/SDK/Engine_functions.cpp)
target_include_directories(qdbridge PRIVATE ${QD_SDK} ${COOPBRIDGE})
target_link_libraries(qdbridge PRIVATE ws2_32 user32)
