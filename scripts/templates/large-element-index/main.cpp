// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include <array>
#include <iostream>
#include "test_utils/runner/TestSuite.h"
#include "util/OSWindow.h"

void ANGLEProcessTestArgs(int* argc, char* argv[]);

int main(int argc, char** argv) {
    ANGLEProcessTestArgs(&argc, argv);
    // Only the bounded large-index test suites are registered in this target.
    angle::TestSuite suite(&argc, argv, [] {});
    constexpr char expectations[] = "src/tests/angle_end2end_tests_expectations.txt";
    std::array<char, 512> path;
    if (!angle::FindTestDataPath(expectations, path.data(), path.size())) {
        std::cerr << "Unable to find pinned ANGLE expectations\n";
        return 1;
    }
    suite.setTestExpectationsAllowMask(angle::GPUTestExpectationsParser::kGpuTestSkip |
                                      angle::GPUTestExpectationsParser::kGpuTestTimeout |
                                      angle::GPUTestExpectationsParser::kGpuTestPass);
    if (!suite.loadAllTestExpectationsFromFile(std::string(path.data()))) {
        return 1;
    }
    return suite.run();
}
