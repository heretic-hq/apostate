// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include <array>
#include <cstring>
#include <limits>

#include "platform/PlatformMethods.h"
#include "test_utils/ANGLETest.h"
#include "test_utils/gl_raii.h"

using namespace angle;
namespace
{
constexpr GLint64 kDefaultLimit = (GLint64{1} << 30) - 1;
constexpr GLint64 kProfileLimit = 4294967294LL;
constexpr std::array<GLuint, 12> kIndices = {
    0u, 1u, 0xffffffu, 0x3ffffffeu, 0x3fffffffu, 0x40000000u,
    0x7fffffffu, 0x80000000u, 0xfffffffcu, 0xfffffffdu, 0xfffffffeu, 0xffffffffu};

bool IndexProfileCap(PlatformMethods *, const char *name, int64_t *value)
{
    if (name && value && std::strcmp(name, "MAX_ELEMENT_INDEX") == 0)
    {
        *value = kProfileLimit;
        return true;
    }
    return false;
}

class LargeIndexBase : public ANGLETest<>
{
  public:
    LargeIndexBase(bool profile, bool webgl, bool robust, bool noError = false)
        : mPrevious(gDefaultPlatformMethods.getProfileIntegerCapV1)
    {
        gDefaultPlatformMethods.getProfileIntegerCapV1 =
            profile ? IndexProfileCap : DefaultGetProfileIntegerCapV1;
        forceNewDisplay();
        setWindowWidth(7);
        setWindowHeight(7);
        setConfigRedBits(8);
        setConfigGreenBits(8);
        setConfigBlueBits(8);
        setConfigAlphaBits(8);
        setWebGLCompatibilityEnabled(webgl);
        setRobustAccess(robust);
        setRobustResourceInit(true);
        setNoErrorEnabled(noError);
    }
    ~LargeIndexBase() override
    {
        gDefaultPlatformMethods.getProfileIntegerCapV1 = mPrevious;
    }

  protected:
    GLint64 limit()
    {
        GLint64 value = 0;
        glGetInteger64v(GL_MAX_ELEMENT_INDEX, &value);
        EXPECT_GL_NO_ERROR();
        return value;
    }
    void prepare()
    {
        constexpr char vertex[] = R"(#version 300 es
layout(location=0) in vec4 p;
uniform uvec2 expected;
uniform bool mixed;
flat out uint id;
void main() {
    id=uint(gl_VertexID);
    gl_Position=p;
    if (mixed) gl_Position.x += id==expected.x ? -0.5 : 0.5;
    gl_PointSize=1.0;
})";
        constexpr char fragment[] = R"(#version 300 es
precision highp float;
precision highp int;
flat in uint id;
uniform uvec2 expected;
out vec4 color;
void main() {color=(id==expected.x || id==expected.y)?vec4(0,1,0,1):vec4(1,0,0,1);}
)";
        mProgram = CompileProgram(vertex, fragment);
        ASSERT_NE(mProgram, 0u);
        glUseProgram(mProgram);
        mExpected = glGetUniformLocation(mProgram, "expected");
        mMixed = glGetUniformLocation(mProgram, "mixed");
        ASSERT_NE(mExpected, -1);
        ASSERT_NE(mMixed, -1);
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, mIndices);
        glBindBuffer(GL_ARRAY_BUFFER, mVertices);
        constant();
        ASSERT_GL_NO_ERROR();
    }
    void constant()
    {
        glDisableVertexAttribArray(0);
        glVertexAttrib4f(0, 0, 0, 0, 1);
        glVertexAttribDivisor(0, 0);
    }
    void buffer(GLuint divisor, bool convert)
    {
        glBindBuffer(GL_ARRAY_BUFFER, mVertices);
        if (convert)
        {
            // Two RGB signed-byte records exercise non-native vertex formats;
            // the absent fourth component must still become1.0.
            constexpr std::array<GLbyte, 6> values = {0, 0, 0, 0, 0, 0};
            glBufferData(GL_ARRAY_BUFFER, sizeof(values), values.data(), GL_STATIC_DRAW);
            glVertexAttribPointer(0, 3, GL_BYTE, GL_FALSE, 0, nullptr);
        }
        else
        {
            constexpr std::array<GLfloat, 8> values = {0, 0, 0, 1, 0, 0, 0, 1};
            glBufferData(GL_ARRAY_BUFFER, sizeof(values), values.data(), GL_STATIC_DRAW);
            glVertexAttribPointer(0, 4, GL_FLOAT, GL_FALSE, 0, nullptr);
        }
        glEnableVertexAttribArray(0);
        glVertexAttribDivisor(0, divisor);
    }
    void clear()
    {
        glClearColor(0, 0, 1, 1);
        glClear(GL_COLOR_BUFFER_BIT);
        ASSERT_GL_NO_ERROR();
    }
    void point(GLuint index, bool instanced, GLenum wanted)
    {
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(index), &index, GL_STATIC_DRAW);
        glUniform2ui(mExpected, index, index);
        glUniform1i(mMixed, GL_FALSE);
        clear();
        if (instanced)
            glDrawElementsInstanced(GL_POINTS, 1, GL_UNSIGNED_INT, nullptr, 2);
        else
            glDrawElements(GL_POINTS, 1, GL_UNSIGNED_INT, nullptr);
        EXPECT_GL_ERROR(wanted);
        EXPECT_PIXEL_COLOR_EQ(3, 3, wanted == GL_NO_ERROR && index != 0xffffffffu
                                         ? GLColor::green : GLColor::blue);
        EXPECT_GL_NO_ERROR();
    }
    void mixed(GLuint first, GLuint second, bool ranged, bool instanced)
    {
        const std::array<GLuint, 2> data = {first, second};
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(data), data.data(), GL_STATIC_DRAW);
        glUniform2ui(mExpected, first, second);
        glUniform1i(mMixed, GL_TRUE);
        clear();
        if (ranged)
            glDrawRangeElements(GL_POINTS, first, second, 2, GL_UNSIGNED_INT, nullptr);
        else if (instanced)
            glDrawElementsInstanced(GL_POINTS, 2, GL_UNSIGNED_INT, nullptr, 2);
        else
            glDrawElements(GL_POINTS, 2, GL_UNSIGNED_INT, nullptr);
        ASSERT_GL_NO_ERROR();
        EXPECT_PIXEL_COLOR_EQ(1, 3, GLColor::green);
        EXPECT_PIXEL_COLOR_EQ(5, 3, GLColor::green);
    }
    void testTearDown() override
    {
        if (mProgram != 0) glDeleteProgram(mProgram);
    }
    GLuint mProgram = 0;
    GLint mExpected = -1;
    GLint mMixed = -1;
    GLBuffer mIndices;
    GLBuffer mVertices;
    GetProfileIntegerCapV1Func mPrevious;
};
}  // namespace

class LargeElementIndexProfileTest : public LargeIndexBase
{
  public:
    LargeElementIndexProfileTest() : LargeIndexBase(true, true, false) {}
};
class LargeElementIndexUnprofiledTest : public LargeIndexBase
{
  public:
    LargeElementIndexUnprofiledTest() : LargeIndexBase(false, true, false) {}
};
class LargeElementIndexNativeTest : public LargeIndexBase
{
  public:
    LargeElementIndexNativeTest() : LargeIndexBase(true, false, false) {}
};
class LargeElementIndexRobustTest : public LargeIndexBase
{
  public:
    LargeElementIndexRobustTest() : LargeIndexBase(true, true, true) {}
};
class LargeElementIndexNoErrorTest : public LargeIndexBase
{
  public:
    LargeElementIndexNoErrorTest() : LargeIndexBase(true, false, false, true) {}
};

TEST_P(LargeElementIndexProfileTest, ConstantAttributeBoundaries)
{
    ASSERT_EQ(limit(), kProfileLimit);
    prepare();
    for (GLuint index : kIndices)
    {
        SCOPED_TRACE(index);
        point(index, false, index == 0xfffffffeu ? GL_INVALID_OPERATION : GL_NO_ERROR);
    }
}

TEST_P(LargeElementIndexProfileTest, InstanceDivisorsAndFormatConversion)
{
    ASSERT_EQ(limit(), kProfileLimit);
    prepare();
    for (GLuint divisor : {1u, 2u, 256u, 0xffffffffu})
    {
        SCOPED_TRACE(divisor);
        for (bool convert : {false, true})
        {
            SCOPED_TRACE(convert);
            buffer(divisor, convert);
            for (GLuint index : kIndices)
            {
                SCOPED_TRACE(index);
                point(index, true, index == 0xfffffffeu ? GL_INVALID_OPERATION : GL_NO_ERROR);
            }
        }
    }
}

TEST_P(LargeElementIndexProfileTest, MixedWideRangesAndDrawRangeElements)
{
    ASSERT_EQ(limit(), kProfileLimit);
    prepare();
    for (GLuint first : {0u, 0x80000000u})
    {
        for (bool ranged : {false, true})
        {
            constant();
            mixed(first, 0xfffffffdu, ranged, false);
        }
        buffer(256, true);
        mixed(first, 0xfffffffdu, false, true);
    }
}

TEST_P(LargeElementIndexProfileTest, SmallPerVertexBufferRemainsBounded)
{
    ASSERT_EQ(limit(), kProfileLimit);
    prepare();
    for (bool convert : {false, true})
    {
        buffer(0, convert);
        for (GLuint index : kIndices)
        {
            point(index, false, index > 1u && index != 0xffffffffu
                                    ? GL_INVALID_OPERATION : GL_NO_ERROR);
        }
    }
}

TEST_P(LargeElementIndexProfileTest, PrimitiveRestartPreservesLargeIndices)
{
    ASSERT_EQ(limit(), kProfileLimit);
    prepare();
    for (GLuint index : {0u, 0xfffffffdu})
    {
        const std::array<GLuint, 2> data = {index, 0xffffffffu};
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(data), data.data(), GL_STATIC_DRAW);
        glUniform2ui(mExpected, index, index);
        clear();
        glDrawElements(GL_POINTS, 2, GL_UNSIGNED_INT, nullptr);
        ASSERT_GL_NO_ERROR();
        EXPECT_PIXEL_COLOR_EQ(3, 3, GLColor::green);
    }
}

TEST_P(LargeElementIndexProfileTest, LineLoopPreservesLargeIndices)
{
    ASSERT_EQ(limit(), kProfileLimit);
    prepare();
    const std::array<GLuint, 3> data = {0u, 0xfffffffdu, 0xffffffffu};
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(data), data.data(), GL_STATIC_DRAW);
    glUniform2ui(mExpected, data[0], data[1]);
    glUniform1i(mMixed, GL_TRUE);
    clear();
    glDrawElements(GL_LINE_LOOP, 3, GL_UNSIGNED_INT, nullptr);
    ASSERT_GL_NO_ERROR();
    EXPECT_PIXEL_COLOR_EQ(3, 3, GLColor::green);
}

TEST_P(LargeElementIndexUnprofiledTest, DefaultLimitUnchanged)
{
    EXPECT_EQ(limit(), kDefaultLimit);
}
TEST_P(LargeElementIndexNativeTest, DefaultLimitUnchanged)
{
    EXPECT_EQ(limit(), kDefaultLimit);
}
TEST_P(LargeElementIndexRobustTest, DefaultLimitUnchanged)
{
    EXPECT_EQ(limit(), kDefaultLimit);
}
TEST_P(LargeElementIndexNoErrorTest, DefaultLimitUnchanged)
{
    EXPECT_EQ(limit(), kDefaultLimit);
}

ANGLE_INSTANTIATE_TEST(LargeElementIndexProfileTest, ES3_VULKAN_SWIFTSHADER());
ANGLE_INSTANTIATE_TEST(LargeElementIndexUnprofiledTest, ES3_VULKAN_SWIFTSHADER());
ANGLE_INSTANTIATE_TEST(LargeElementIndexNativeTest, ES3_VULKAN_SWIFTSHADER());
ANGLE_INSTANTIATE_TEST(LargeElementIndexRobustTest, ES3_VULKAN_SWIFTSHADER());
ANGLE_INSTANTIATE_TEST(LargeElementIndexNoErrorTest, ES3_VULKAN_SWIFTSHADER());
