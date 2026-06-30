#pragma once

#include "KYVPDefines.h"
#include "KYVPLibTL.h"
#include "KYIMGPROC_PixelFormatDecoder_Defines.h"

#ifdef _MSC_VER
#ifdef KYVPIMAGEPROCESSING_EXPORTS
#define KYVPIMAGEPROCESSING_EXTERNAL_DLL __declspec(dllexport)
#else
#define KYVPIMAGEPROCESSING_EXTERNAL_DLL __declspec(dllimport)
#endif
#else
#define KYVPIMAGEPROCESSING_EXTERNAL_DLL
#endif

#define KYIMGPROC_API KYVP_EXTERNAL_C KYVPIMAGEPROCESSING_EXTERNAL_DLL
#define KYIMGPROC_VAR KYVP_EXTERNAL KYVPIMAGEPROCESSING_EXTERNAL_DLL

/**
 * @page KYImgProc Image Processing Library
 * @subpage KYImgProc_Introduction <br>
 * @subpage KYImgProc_Functions <br>
 * @subpage KYVPImageProcessingStructs <br>
 */

 /**
  * @page KYImgProc_Functions Image Processing Library Functions
  * @ref KYImgProc_InitFunctions <br>
  * @ref KYImgProc_UninitFunctions <br>
  * @ref KYImgProc_ConversationFunctions <br>
  * @ref KYImgProc_SaveAndRecord <br>
  * @ref KYImgProc_GetFunctions <br>
  * @ref KYImgProc_ComparisonFunctions <br>
  */

  /**
   * @page KYImgProc_Introduction Introduction
   * @includedoc KYVP_Image_Processing_library.html <br> <br>
   */

/////////////////////////////////////////////////////////////////////////////////
/// Macros alloc KYIMGPROC_ConvertParam_Args
/////////////////////////////////////////////////////////////////////////////////

#define DECL_PIXELIF(_name)                             KYVP_VIDEO_PIXELIF _name;

#define INIT_PIXEL_IF(_name)                            _name.data_width    =   KYVP_DATA_WIDTH_UNKNOWN;\
                                                        _name.data_subtype  =   KYVP_DATA_SUBTYPE_INVALID;\
                                                        _name.data_type     =   KYVP_DATA_TYPE_INVALID;

#define DECL_VP_PIXEL_FORMAT(_name)                     KYVP_PIXEL_FORMAT _name; DECL_PIXELIF(_name##pVPPixelIF) 

#define INIT_VP_PIXEL_FORMAT(_name)                     INIT_PIXEL_IF(_name##pVPPixelIF)\
                                                        _name.version       =   KYIMGPROC_KYVP_PIXEL_FORMAT_STRUCT_VERSION;\
                                                        _name.pVPPixelIF    =   _name##pVPPixelIF;

#define DECL_PIXEL_DECODER(_name)                       DECL_VP_PIXEL_FORMAT(_name##_pVP_PIXEL_FORMAT)\
                                                        KYIMGPROC_PIXEL_DECODER _name;

#define INIT_PIXEL_DECODER(_name)                       INIT_VP_PIXEL_FORMAT(_name##_pVP_PIXEL_FORMAT)\
                                                        _name.version       =   KYIMGPROC_PIXEL_DECODER_STRUCT_VERSION;\
                                                        _name.pVP_PIXEL_FORMAT = &_name##_pVP_PIXEL_FORMAT;

#define DECL_VPIMAGE(_name)                             DECL_PIXEL_DECODER(_name##_pixelFormat)\
                                                        VPImage _name; 

#define INIT_VPIMAGE(_name)                             INIT_PIXEL_DECODER(_name##_pixelFormat)\
                                                        _name.uVersion      =   KYIMGPROC_VPIMAGE_STRUCT_VERSION;\
                                                        _name.pixelFormat   = &_name##_pixelFormat;\

#define DECL_CONVERT_PARAMS(_name)                      DECL_VPIMAGE(_name##_srcImage)\
                                                        DECL_VPIMAGE(_name##_dstImage)\
                                                        KYIMGPROC_ConvertParam_Args _name;

#define INIT_CONVERT_PARAMS(_name)                      _name.srcImage     =    &_name##_srcImage; _name.dstImage = &_name##_dstImage;\
                                                        _name.uVersion = KYIMGPROC_CONVERTPARAM_STRUCT_VERSION;\
                                                        INIT_VPIMAGE(_name##_srcImage)\
                                                        INIT_VPIMAGE(_name##_dstImage)

#define CONVERT_PARAMS_DECL_AND_INIT(params)            DECL_CONVERT_PARAMS(params) INIT_CONVERT_PARAMS(params)

/////////////////////////////////////////////////////////////////////////////////
/// Macros alloc KYIMGPROC_saveImage
/////////////////////////////////////////////////////////////////////////////////

#define DECL_SAVE_IMAGE_PARAMS(_name)                   DECL_VPIMAGE(_name##_srcImage)\
                                                        KYIMGPROC_SaveImageArgs _name;

#define INIT_SAVE_IMAGE_PARAMS(_name)                   _name.srcImage = &_name##_srcImage;\
                                                        _name.uVersion = KYIMGPROC_SAVEIMAGE_STRUCT_VERSION;\
                                                        INIT_VPIMAGE(_name##_srcImage)

#define SAVE_IMAGE_PARAMS_DECL_AND_INIT(params)         DECL_SAVE_IMAGE_PARAMS(params) INIT_SAVE_IMAGE_PARAMS(params)

/////////////////////////////////////////////////////////////////////////////////
/// Macros alloc KYIMGPROC_saveVideo
/////////////////////////////////////////////////////////////////////////////////

#define DECL_SAVE_VIDEO_PARAMS(_name)                   DECL_PIXEL_DECODER(_name##_pixelFormat)\
                                                        KYIMGPROC_SaveVideoArgs _name;

#define INIT_SAVE_VIDEO_PARAMS(_name)                   _name.uVersion = KYIMGPROC_SAVEVIDEO_STRUCT_VERSION;\
                                                        INIT_PIXEL_DECODER(_name##_pixelFormat)\
                                                        _name.srcPixelFormat = &_name##_pixelFormat;

#define SAVE_VIDEO_PARAMS_DECL_AND_INIT(params)         DECL_SAVE_VIDEO_PARAMS(params) INIT_SAVE_VIDEO_PARAMS(params)

#if defined(_MSC_VER)
#  pragma warning(push)
#  pragma warning(disable:4514) /* unreferenced inline function has been removed */
#endif

 //////////////////////////////////////////////////////////////////////
 /// KYIMGPROC_VIDEO_RECORDER_HANDLE DEFINES
 //////////////////////////////////////////////////////////////////////

#define KYIMGPROC_VIDEO_RECORDER_HANDLE_INVALID_INIT_VALUE ((void*)-1)
#define KYIMGPROC_VIDEO_RECORDER_HANDLE_IS_INVALI(_hHandle)                        KYImgProc_VideoRecorderHandleIsInvalid(_hHandle)
#define KYIMGPROC_VIDEO_RECORDER_HANDLE_IS_NULL(_hHandle)                          KYImgProc_VideoRecorderHandleIsNull(_hHandle)
#define KYIMGPROC_VIDEO_RECORDER_HANDLE_IS_EQUAL(_hHandleFirst, _hHandleSecond)    KYImgProc_VideoRecorderHandleIsEqual(_hHandleFirst, _hHandleSecond)

/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_VIDEO_RECORDER_HANDLE KYIMGPROC_VIDEO_RECORDER_HANDLE
 * | Structure Field    | Description                       | Remarks   |
 * | -------------      | -----------                       | ----      |
 * | pNativePointer     | Pointer to video recorder Handle  |           |
 */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_VIDEO_RECORDER_HANDLE
{
    #ifdef __cplusplus
    _KYIMGPROC_VIDEO_RECORDER_HANDLE()
        : pNativePointer(KYIMGPROC_VIDEO_RECORDER_HANDLE_INVALID_INIT_VALUE)
    {
    }
protected:
    _KYIMGPROC_VIDEO_RECORDER_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
    #endif
    void* pNativePointer;
}KYIMGPROC_VIDEO_RECORDER_HANDLE;
#pragma pack(pop)

KYIMGPROC_VAR const KYIMGPROC_VIDEO_RECORDER_HANDLE KYIMGPROC_VIDEO_RECORDER_HANDLE_NULL;
KYIMGPROC_VAR const KYIMGPROC_VIDEO_RECORDER_HANDLE KYIMGPROC_VIDEO_RECORDER_HANDLE_INVALID;

//////////////////////////////////////////////////////////////////////
/// KYIMGPROC_IMAGE_CONVERTER_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#define KYIMGPROC_IMAGE_CONVERTER_HANDLE_INVALID_INIT_VALUE ((void*)-1)
#define KYIMGPROC_IMAGE_CONVERTER_HANDLE_IS_INVALID(_hHandle)                      KYImgProc_ImageConverterHandleIsInvalid(_hHandle)
#define KYIMGPROC_IMAGE_CONVERTER_HANDLE_IS_NULL(_hHandle)                         KYImgProc_ImageConverterHandleIsNull(_hHandle)
#define KYIMGPROC_IMAGE_CONVERTER_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)  KYImgProc_ImageConverterHandlesIsEqual(_hHandleFirst, _hHandleSecond)

 /**
  * @page KYVPImageProcessingStructs Image Processing Library Structs
  * @section KYIMGPROC_IMAGE_CONVERTER_HANDLE KYIMGPROC_IMAGE_CONVERTER_HANDLE
  * | Structure Field    | Description                       | Remarks   |
  * | -------------      | -----------                       | ----      |
  * | pNativePointer     | Pointer to image converter Handle |           |
  */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_IMAGE_CONVERTER_HANDLE
{
    #ifdef __cplusplus
    _KYIMGPROC_IMAGE_CONVERTER_HANDLE()
        : pNativePointer(KYIMGPROC_IMAGE_CONVERTER_HANDLE_INVALID_INIT_VALUE)
    {
    }

    constexpr
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYIMGPROC_IMAGE_CONVERTER_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
    #endif
    void* pNativePointer;

} KYIMGPROC_IMAGE_CONVERTER_HANDLE;
#pragma pack(pop)

KYIMGPROC_VAR const KYIMGPROC_IMAGE_CONVERTER_HANDLE KYIMGPROC_IMAGE_CONVERTER_HANDLE_NULL;
KYIMGPROC_VAR const KYIMGPROC_IMAGE_CONVERTER_HANDLE KYIMGPROC_IMAGE_CONVERTER_HANDLE_INVALID;

//////////////////////////////////////////////////////////////////////
/// ENUMS
//////////////////////////////////////////////////////////////////////

/**
 * @page KYVPDefines Defines
 * @section BITS_DEPTH_SHIFT_MODE BITS_DEPTH_SHIFT_MODE
 * Defines how pixel data with less than 16 bits per channel is aligned when expanded to 16 bits
 *
 * | Enumerator                         | Value  | Description                                                                 |
 * | ---------------------------------- | ------ | --------------------------------------------------------------------------- |
 * | BITS_DEPTH_SHIFT_MODE_UNKNOWN      | 0x00   | Bit shift alignment mode is unspecified or unknown                          |
 * | BITS_DEPTH_SHIFT_MODE_LSB          | 0x01   | Data is LSB-aligned: meaningful bits are in the least significant bits      |
 * | BITS_DEPTH_SHIFT_MODE_MSB          | 0x02   | Data is MSB-aligned: meaningful bits are in the most significant bits       |
 */
typedef enum _BITS_DEPTH_SHIFT_MODE
{
    BITS_DEPTH_SHIFT_MODE_UNKNOWN   = 0x00,
    BITS_DEPTH_SHIFT_MODE_LSB       = 0x01,
    BITS_DEPTH_SHIFT_MODE_MSB       = 0x02
} BITS_DEPTH_SHIFT_MODE;

/**
 * @page KYVPDefines Defines
 * @section SAVED_IMAGE_FORMAT SAVED_IMAGE_FORMAT
 *
 * This enumeration defines the supported formats for saving images.
 * It is used to specify the desired file format when exporting images to disk.
 *
 * | Enumerator                         | Value  | Description                                                                 |
 * | ---------------------------------- | ------ | --------------------------------------------------------------------------- |
 * | SAVED_IMAGE_FORMAT_UNKNOWN         | 0x00   | Unknown image format. Used when the format cannot be determined or is invalid |
 * | SAVED_IMAGE_FORMAT_BMP             | 0x01   | Bitmap image format (.bmp). Uncompressed format widely supported           |
 * | SAVED_IMAGE_FORMAT_PNG             | 0x02   | Portable Network Graphics format (.png). Lossless compression with transparency support |
 * | SAVED_IMAGE_FORMAT_TIF             | 0x03   | Tagged Image File Format (.tif or .tiff). A flexible format commonly used in image analysis and processing |
 */
typedef enum _SAVED_IMAGE_FORMAT
{
    SAVED_IMAGE_FORMAT_UNKNOWN   = 0x00,
    SAVED_IMAGE_FORMAT_BMP       = 0x01,
    SAVED_IMAGE_FORMAT_PNG       = 0x02,
    SAVED_IMAGE_FORMAT_TIF       = 0x03,
} SAVED_IMAGE_FORMAT;

//////////////////////////////////////////////////////////////////////
/// STRUCTURES
//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_VPIMAGE_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_VPIMAGE_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section VPImage VPImage
 * | Structure Field    | Type                      | Description                               | Remarks                                           |
 * | -------------      | -----                     | -----------                               | ----                                              |
 * | uVersion           | uint32_t                  | Struct version                            | Must be between 1 and VP_IMAGE_MAX_VERSION        |
 * | uWidth             | uint32_t                  | Image width                               |                                                   |
 * | uHeight            | uint32_t                  | Image height                              |                                                   |
 * | pBuffer            | void*                     | Image data buffer                         | Pointer to image data                             |
 * | uBufferSize        | size_t                    | Image data buffer size bytes              | Pointer to image data                             |
 * | pixelFormat        | KYIMGPROC_PIXEL_DECODER   | pointer to KYIMGPROC_PIXEL_DECODER struct |                                                   |
 */
#pragma pack(push, 1)
typedef struct _VPImage
{
    uint32_t                                    uVersion        KYVP_MEMBER_DEFAULT(KYIMGPROC_VPIMAGE_STRUCT_VERSION);
    uint32_t                                    uWidth          KYVP_MEMBER_DEFAULT(0);
    uint32_t                                    uHeight         KYVP_MEMBER_DEFAULT(0);
    void*                                       pBuffer         KYVP_MEMBER_DEFAULT(nullptr);
    size_t                                      uBufferSize     KYVP_MEMBER_DEFAULT(0);
    KYIMGPROC_PIXEL_DECODER*                    pixelFormat     KYVP_MEMBER_DEFAULT(nullptr);
} VPImage;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_PACKEDDATAINFO_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_PACKEDDATAINFO_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section PackedDataInfo PackedDataInfo
 * | Structure Field        | Type                              | Description                               | Remarks       |
 * | -------------          | -----                             | -----------                               | ----          |
 * | uVersion               | uint32_t                          | Version of the structure                  |               |
 * | ePackedModeType        | DS_PACKED_DATA_MODE               | Type of the packed data format            |               |
 * | eProcessingModeType    | DS_PACKED_DATA_PROCESSING_MODE    | The type of processing packed data (Trim to MSB8, expand bits to 16) |         |
 * | pPixelFormat           | const KYIMGPROC_PIXEL_DECODER*    | The const pointer to KYIMGPROC_PIXEL_DECODER struct | |
 * | uWidth                 | size_t                            | Image width                               |               |
 * | uHeight                | size_t                            | Image height                              |               |
 * | uUnpackedBufferSize    | size_t                            | Size of buffer required to store unpacked data |          |
 */
#pragma pack(push, 1)
typedef struct _PackedDataInfo
{
    uint32_t                                    uVersion                KYVP_MEMBER_DEFAULT(KYIMGPROC_PACKEDDATAINFO_STRUCT_VERSION);
    DS_PACKED_DATA_MODE                         ePackedModeType         KYVP_MEMBER_DEFAULT(DS_PACKED_DATA_MODE_UNPACKED);
    DS_PACKED_DATA_PROCESSING_MODE              eProcessingModeType     KYVP_MEMBER_DEFAULT(DS_PACKED_DATA_PROCESSING_MODE_EXPAND16);
    const KYIMGPROC_PIXEL_DECODER*              pPixelFormat            KYVP_MEMBER_DEFAULT(nullptr);
    size_t                                      uWidth                  KYVP_MEMBER_DEFAULT(0);
    size_t                                      uHeight                 KYVP_MEMBER_DEFAULT(0);
    size_t                                      uUnpackedBufferSize     KYVP_MEMBER_DEFAULT(0);
} PackedDataInfo;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

//////////////////////////////////////////////////////////////////////
/// ARGUMENT STRUCTURES
//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_INITVIDEORECORDER_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_INITVIDEORECORDER_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_InitVideoRecorderArgs KYIMGPROC_InitVideoRecorderArgs
 * | Structure Field    | Type                              | Description                                       | Remarks   |
 * | -------------      | -----                             | -----------                                       | ----      |
 * | uVersion           | uint32_t                          | Version of the structure                          |           |
 * | phRecordHandle     | KYIMGPROC_VIDEO_RECORDER_HANDLE*  | pointer to @ref KYIMGPROC_VIDEO_RECORDER_HANDLE   |           |
 */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_InitVideoRecorderArgs
{
    uint32_t                                    uVersion            KYVP_MEMBER_DEFAULT(KYIMGPROC_INITVIDEORECORDER_STRUCT_VERSION);
    KYIMGPROC_VIDEO_RECORDER_HANDLE*            phRecordHandle      KYVP_MEMBER_DEFAULT(nullptr);
} KYIMGPROC_InitVideoRecorderArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_DELETEVIDEORECORDER_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_DELETEVIDEORECORDER_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_DeleteVideoRecorderArgs KYIMGPROC_DeleteVideoRecorderArgs
 * | Structure Field    | Type                              | Description                                       | Remarks   |
 * | -------------      | -----                             | -----------                                       | ----      |
 * | uVersion           | uint32_t                          | Version of the structure                          |           |
 * | hRecordHandle      | KYIMGPROC_VIDEO_RECORDER_HANDLE   | @ref KYIMGPROC_VIDEO_RECORDER_HANDLE object       |           |
 */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_DeleteVideoRecorderArgs
{
    uint32_t                                    uVersion            KYVP_MEMBER_DEFAULT(KYIMGPROC_DELETEVIDEORECORDER_STRUCT_VERSION);
    KYIMGPROC_VIDEO_RECORDER_HANDLE             phRecordHandle;
}KYIMGPROC_DeleteVideoRecorderArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_INITIMAGECONVERTER_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_INITIMAGECONVERTER_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_InitImageConverterArgs KYIMGPROC_InitImageConverterArgs
 * | Structure Field    | Type                              | Description                                       | Remarks   |
 * | -------------      | -----                             | -----------                                       | ----      |
 * | uVersion           | uint32_t                          | Version of the structure                          |           |
 * | hRecordHandle      | KYIMGPROC_IMAGE_CONVERTER_HANDLE  | @ref KYIMGPROC_IMAGE_CONVERTER_HANDLE object      |           |
 */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_InitImageConverterArgs
{
    uint32_t                                    uVersion            KYVP_MEMBER_DEFAULT(KYIMGPROC_INITIMAGECONVERTER_STRUCT_VERSION);
    KYIMGPROC_IMAGE_CONVERTER_HANDLE*           phImageProcHandle   KYVP_MEMBER_DEFAULT(nullptr);
} KYIMGPROC_InitImageConverterArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_DELETEIMAGECONVERTER_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_DELETEIMAGECONVERTER_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_DeleteImageConverterArgs KYIMGPROC_DeleteImageConverterArgs
 * | Structure Field    | Type                              | Description                                       | Remarks   |
 * | -------------      | -----                             | -----------                                       | ----      |
 * | uVersion           | uint32_t                          | Version of the structure                          |           |
 * | hRecordHandle      | KYIMGPROC_IMAGE_CONVERTER_HANDLE  | @ref KYIMGPROC_IMAGE_CONVERTER_HANDLE object      |           |
 */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_DeleteImageConverterArgs
{
    uint32_t                                    uVersion            KYVP_MEMBER_DEFAULT(0);
    KYIMGPROC_IMAGE_CONVERTER_HANDLE            hHandle;
}KYIMGPROC_DeleteImageConverterArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_CONVERTPARAM_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_CONVERTPARAM_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_ConvertParam_Args KYIMGPROC_ConvertParam_Args
 * | Structure Field    | Type                              | Description                               | Remarks   |
 * | -------------      | -----                             | -----------                               | ----      |
 * | uVersion           | uint32_t                          | Version of the structure                  |           |
 * | srcImage           | VPImage                           | Pointer to source image struct            |           |
 * | dstImage           | VPImage                           | Pointer to destination image struct       |           |
 * | h_ImgProcHandle    | KYIMGPROC_IMAGE_CONVERTER_HANDLE  | The handle of an existing converter       |           |
 */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_ConvertParam_Args
{
    uint32_t                                    uVersion            KYVP_MEMBER_DEFAULT(KYIMGPROC_CONVERTPARAM_STRUCT_VERSION);
    VPImage*                                    srcImage            KYVP_MEMBER_DEFAULT(nullptr);
    VPImage*                                    dstImage            KYVP_MEMBER_DEFAULT(nullptr);
    // Since version 2
    KYIMGPROC_IMAGE_CONVERTER_HANDLE            h_ImgProcHandle;
} KYIMGPROC_ConvertParam_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_SAVEIMAGE_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_SAVEIMAGE_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_SaveImageArgs KYIMGPROC_SaveImageArgs
 * | Structure Field    | Type                      | Description                               | Remarks   |
 * | -------------      | -----                     | -----------                               | ----      |
 * | uVersion           | uint32_t                  | Version of the structure                  |           |
 * | srcImage           | VPImage*                  | Pointer to VPImage struct                 |           |
 * | szFileName         | const char*               | FileName                                  |           |
 */

#pragma pack(push, 1)
typedef struct _KYIMGPROC_SaveImageArgs
{
    uint32_t                                    uVersion                KYVP_MEMBER_DEFAULT(KYIMGPROC_SAVEIMAGE_STRUCT_VERSION);
    SAVED_IMAGE_FORMAT                          eImageFormat            KYVP_MEMBER_DEFAULT(SAVED_IMAGE_FORMAT_UNKNOWN);
    BITS_DEPTH_SHIFT_MODE                       eBitsDepthShiftMode     KYVP_MEMBER_DEFAULT(BITS_DEPTH_SHIFT_MODE_UNKNOWN);
    VPImage*                                    srcImage                KYVP_MEMBER_DEFAULT(nullptr);
    const char*                                 szFileName              KYVP_MEMBER_DEFAULT(nullptr);
}KYIMGPROC_SaveImageArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_SAVERAW_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_SAVERAW_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_SaveRawArgs KYIMGPROC_SaveRawArgs
 * | Structure Field    | Type                      | Description                               | Remarks   |
 * | -------------      | -----                     | -----------                               | ----      |
 * | uVersion           | uint32_t                  | Version of the structure                  |           |
 * | srcImage           | VPImage*                  | Pointer to VPImage struct                 |           |
 * | szFileName         | const char*               | FileName                                  |           |
 */

#pragma pack(push, 1)
typedef struct _KYIMGPROC_SaveRawArgs
{
    uint32_t                                    uVersion        KYVP_MEMBER_DEFAULT(KYIMGPROC_SAVERAW_STRUCT_VERSION);
    VPImage*                                    srcImage        KYVP_MEMBER_DEFAULT(nullptr);
    const char*                                 szFileName      KYVP_MEMBER_DEFAULT(nullptr);
}KYIMGPROC_SaveRawArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_SAVEVIDEO_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_SAVEVIDEO_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_SaveVideoArgs KYIMGPROC_SaveVideoArgs
 * | Structure Field    | Type                              | Description                                       | Remarks   |
 * | -------------      | -----                             | -----------                                       | ----      |
 * | uVersion           | uint32_t                          | Version of the structure                          |           |
 * | hRecordHandle      | KYIMGPROC_IMAGE_CONVERTER_HANDLE  | @ref KYIMGPROC_IMAGE_CONVERTER_HANDLE object      |           |
 * | szFileName         | const char*                       | File name                                         |           |
 * | srcPixelFormat     | KYIMGPROC_PIXEL_DECODER*          | Pointer to @ref KYIMGPROC_PIXEL_DECODER           |           |
 * |
 */

#pragma pack(push, 1)
typedef struct _KYIMGPROC_SaveVideoArgs
{
    uint32_t                                    uVersion        KYVP_MEMBER_DEFAULT(KYIMGPROC_SAVEVIDEO_STRUCT_VERSION);
    KYIMGPROC_VIDEO_RECORDER_HANDLE             vHandler;
    const char*                                 szFileName;
    KYIMGPROC_PIXEL_DECODER*                    srcPixelFormat;
    uint32_t                                    bitRate;
    double                                      dFps;
    uint32_t                                    uWidth;
    uint32_t                                    uHeight;
} KYIMGPROC_SaveVideoArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_WRITEVIDEOFRAME_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_WRITEVIDEOFRAME_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_WriteVideoFrameArgs KYIMGPROC_WriteVideoFrameArgs
 * | Structure Field    | Type                              | Description                                       | Remarks   |
 * | -------------      | -----                             | -----------                                       | ----      |
 * | uVersion           | uint32_t                          | Version of the structure                          |           |
 * | vHandler           | KYIMGPROC_VIDEO_RECORDER_HANDLE   | @ref KYIMGPROC_VIDEO_RECORDER_HANDLE object       |           |
 * | pSrcImage          | VPImage*                          | Pointer to @ref VPImage                           |           |
 */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_WriteVideoFrameArgs
{
    uint32_t                                    uVersion        KYVP_MEMBER_DEFAULT(KYIMGPROC_WRITEVIDEOFRAME_STRUCT_VERSION);
    KYIMGPROC_VIDEO_RECORDER_HANDLE             vHandler;
    VPImage*                                    pSrcImage;
}KYIMGPROC_WriteVideoFrameArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_STOPVIDEORECORD_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_STOPVIDEORECORD_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYImgProc_StopVideoRecordArgs KYImgProc_StopVideoRecordArgs
 * | Structure Field    | Type                              | Description                                       | Remarks   |
 * | -------------      | -----                             | -----------                                       | ----      |
 * | uVersion           | uint32_t                          | Version of the structure                          |           |
 * | vHandler           | KYIMGPROC_VIDEO_RECORDER_HANDLE   | @ref KYIMGPROC_VIDEO_RECORDER_HANDLE object       |           |
 */
#pragma pack(push, 1)
typedef struct _KYImgProc_StopVideoRecordArgs
{
    uint32_t                                    uVersion        KYVP_MEMBER_DEFAULT(KYIMGPROC_STOPVIDEORECORD_STRUCT_VERSION);
    KYIMGPROC_VIDEO_RECORDER_HANDLE             vHandler;
}KYImgProc_StopVideoRecordArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_GETPACKEDDATAINFO_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_GETPACKEDDATAINFO_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYImgProc_GetPackedDataInfo_Args KYImgProc_GetPackedDataInfo_Args
 * | Structure Field         | Type                             | Description                                                                                 | Remarks |
 * |-------------------------|----------------------------------|---------------------------------------------------------------------------------------------|---------|
 * | uVersion                | uint32_t                         | Version of the structure                                                                    |         |
 * | pPixelFormat            | const KYIMGPROC_PIXEL_DECODER*   | Pointer to a constant KYIMGPROC_PIXEL_DECODER structure describing the source image format  |         |
 * | ePackedModeType         | DS_PACKED_DATA_MODE              | Type of the packed data format                                                              |         |
 * | eProcessingModeType     | DS_PACKED_DATA_PROCESSING_MODE   | The type of processing packed data (Trim to MSB8, expand bits to 16)                        |         |
 * | uWidth                  | size_t                           | Width of the source image (in pixels)                                                       |         |
 * | uHeight                 | size_t                           | Height of the source image (in pixels)                                                      |         |
 * | pPackedDataInfoStruct   | PackedDataInfo*                  | Pointer to an allocated structure that will be filled with unpacked data information        |         |
 */
#pragma pack(push, 1)
typedef struct _KYImgProc_GetPackedDataInfo_Args
{
    uint32_t                                    uVersion                KYVP_MEMBER_DEFAULT(KYIMGPROC_GETPACKEDDATAINFO_STRUCT_VERSION);
    const KYIMGPROC_PIXEL_DECODER*              pPixelFormat            KYVP_MEMBER_DEFAULT(nullptr);
    DS_PACKED_DATA_MODE                         ePackedModeType         KYVP_MEMBER_DEFAULT(DS_PACKED_DATA_MODE_UNPACKED);
    DS_PACKED_DATA_PROCESSING_MODE              eProcessingModeType     KYVP_MEMBER_DEFAULT(DS_PACKED_DATA_PROCESSING_MODE_EXPAND16);
    size_t                                      uWidth                  KYVP_MEMBER_DEFAULT(0);
    size_t                                      uHeight                 KYVP_MEMBER_DEFAULT(0);
    PackedDataInfo*                             pPackedDataInfoStruct   KYVP_MEMBER_DEFAULT(nullptr);
}KYImgProc_GetPackedDataInfo_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_PREPROCCESSPACKEDDATA_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_PREPROCCESSPACKEDDATA_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYImgProc_PreproccessPackedData_Args KYImgProc_PreproccessPackedData_Args
 * | Structure Field         | Type                          | Description                                             | Remarks |
 * |-------------------------|-------------------------------|---------------------------------------------------------|---------|
 * | uVersion                | uint32_t                      | Version of the structure                                |         |
 * | pPackedDataInfoStruct   | const PackedDataInfo*         | Pointer to a constant structure describing the packed data format |         |
 * | pSourceData             | const void*                   | Pointer to the packed image data buffer                 |         |
 * | uSourceDataSize         | size_t                        | Size of the packed image data buffer (in bytes)         |         |
 * | pDestinationData        | void*                         | Pointer to the buffer that will receive the unpacked image data |         |
 * | uDestinationDataSize    | size_t                        | Size of the unpacked image data buffer (in bytes)       |         |
 */
#pragma pack(push, 1)
typedef struct _KYImgProc_PreproccessPackedData_Args
{
    uint32_t                                    uVersion                KYVP_MEMBER_DEFAULT(KYIMGPROC_PREPROCCESSPACKEDDATA_STRUCT_VERSION);
    const PackedDataInfo*                       pPackedDataInfoStruct   KYVP_MEMBER_DEFAULT(nullptr);
    const void*                                 pSourceData             KYVP_MEMBER_DEFAULT(nullptr);
    size_t                                      uSourceDataSize         KYVP_MEMBER_DEFAULT(0);
    void*                                       pDestinationData        KYVP_MEMBER_DEFAULT(nullptr);
    size_t                                      uDestinationDataSize    KYVP_MEMBER_DEFAULT(0);
}KYImgProc_PreproccessPackedData_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_GETPIXELFORMAT_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_GETPIXELFORMAT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYImgProc_GetPixelFormat_Args KYImgProc_GetPixelFormat_Args
 * | Structure Field         | Type                          | Description                                             | Remarks |
 * |-------------------------|-------------------------------|---------------------------------------------------------|---------|
 * | uVersion                | uint32_t                      | Version of the structure                                |         |
 * | uPixelFormatValue       | uint32_t                      | PFNC 32-bit value or CXP 16-bit value                   |         |
 * | pPixelFormatDecoder     | KYIMGPROC_PIXEL_DECODER*      | Pointer to allocated decoder struct                     |         |
 */
#pragma pack(push, 1)
typedef struct _KYImgProc_GetPixelFormat_Args
{
    uint32_t                                    uVersion                                  KYVP_MEMBER_DEFAULT(KYIMGPROC_GETPIXELFORMAT_STRUCT_VERSION);
    uint32_t                                    uPixelFormatValue                         KYVP_MEMBER_DEFAULT(0);
    KYIMGPROC_PIXEL_DECODER*                    pPixelFormatDecoder                       KYVP_MEMBER_DEFAULT(nullptr);
}KYImgProc_GetPixelFormat_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYIMGPROC_GETPIXELFORMATBYNAME_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_GETPIXELFORMATBYNAME_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYImgProc_GetPixelFormatByName_Args KYImgProc_GetPixelFormatByName_Args
 * | Structure Field         | Type                          | Description                                             | Remarks |
 * |-------------------------|-------------------------------|---------------------------------------------------------|---------|
 * | uVersion                | uint32_t                      | Version of the structure                                |         |
 * | pszPixelFormatName      | const char*                   | Pointer to string buffer with PFNC pixel format name    |         |
 * | pPixelFormatDecoder     | KYIMGPROC_PIXEL_DECODER*      | Pointer to allocated decoder struct                     |         |
 */
#pragma pack(push, 1)
typedef struct _KYImgProc_GetPixelFormatByName_Args
{
    uint32_t                                    uVersion                KYVP_MEMBER_DEFAULT(KYIMGPROC_GETPIXELFORMATBYNAME_STRUCT_VERSION);
    const char*                                 pszPixelFormatName      KYVP_MEMBER_DEFAULT(nullptr);
    KYIMGPROC_PIXEL_DECODER*                    pPixelFormatDecoder     KYVP_MEMBER_DEFAULT(nullptr);
}KYImgProc_GetPixelFormatByName_Args;
#pragma pack(pop)

#if defined(_MSC_VER)
#  pragma warning(pop)
#endif

//////////////////////////////////////////////////////////////////////
/// KYIMGPROC API FUNCTIONS
//////////////////////////////////////////////////////////////////////

/// @defgroup KYImgProc_InitFunctions Image processing Init functions
/// @{
/**
 * @section KYImgProc_InitImageConverter KYImgProc_InitImageConverter()
 * Init new instance for image conversion
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_InitImageConverter(KYIMGPROC_InitImageConverterArgs* _InitImageConverterArg) @endcode
 * @param [in,out] _InitImageConverterArgs pointer to @ref KYIMGPROC_InitImageConverterArgs
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_InitImageConverter(KYIMGPROC_InitImageConverterArgs* _pArgs);
/**
 * @section KYImgProc_InitVideoRecorder KYImgProc_InitVideoRecorder()
 * Init new instance for video recording 
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_InitVideoRecorder(KYIMGPROC_InitVideoRecorderArgs* _args) @endcode
 * @param [in,out] _args pointer to @ref KYIMGPROC_InitVideoRecorderArgs
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_InitVideoRecorder(KYIMGPROC_InitVideoRecorderArgs* _pArgs);
/// @}

/// @defgroup KYImgProc_UninitFunctions Image processing Init functions
/// @{
/**
 * @section KYImgProc_DeleteImageConverter KYImgProc_DeleteImageConverter()
 * Delete Image Converter Instance
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_DeleteImageConverter(KYIMGPROC_DeleteImageConverterArgs _DeleteImageConverterArgs) @endcode
 * @param [in,out] _DeleteImageConverterArgs pointer to @ref KYIMGPROC_DeleteImageConverterArgs
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_DeleteImageConverter(KYIMGPROC_DeleteImageConverterArgs* _pArgs);
/**
 * @section KYImgProc_DeleteVideoRecorder KYImgProc_DeleteVideoRecorder()
 * Delete video recorder instance
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_DeleteVideoRecorder(KYIMGPROC_DeleteVideoRecorderArgs* _args) @endcode
 * @param [in,out] _DeleteVireoRecorederArgs pointer to @ref KYIMGPROC_DeleteVideoRecorderArgs
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_DeleteVideoRecorder(KYIMGPROC_DeleteVideoRecorderArgs* _pArgs);
/// @}





//////////////////////////////////////////////////////////////////////////////////////////////////////////////
/// GET FUNCTIONS
//////////////////////////////////////////////////////////////////////////////////////////////////////////////

/// @defgroup KYImgProc_GetFunctions Image processing Get functions
/// @{
/**
 * @section KYImgProc_GetPixelFormat KYImgProc_GetPixelFormat()
 * Get pixel format from specific uint_32t value. 
 * Fill KYIMGPROC_PIXEL_DECODER->_uValue by uint32_t from documentation https://www.emva.org/wp-content/uploads/GenICamPixelFormatValues.pdf and call function
 * @subsection C C
 * @code{.c} KYImgProc_GetPixelFormat(KYIMGPROC_PIXEL_DECODER* resDecoderStruct) @endcode
 * @param [in,out] resDecoderStruct Decoder struct
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_GetPixelFormat(KYImgProc_GetPixelFormat_Args* _pArgs);

/**
 * @section KYImgProc_GetPixelFormatByName KYImgProc_GetPixelFormatByName()
 * Get pixel format from specific char* value.
 * Fill KYIMGPROC_PIXEL_DECODER->_name by name from documentation https://www.emva.org/wp-content/uploads/GenICamPixelFormatValues.pdf and call function
 * @subsection C C
 * @code{.c} KYImgProc_GetPixelFormat(KYIMGPROC_PIXEL_DECODER* resDecoderStruct) @endcode
 * @param [in,out] resDecoderStruct Decoder struct
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_GetPixelFormatByName(KYImgProc_GetPixelFormatByName_Args* _pArgs);
/// @}





//////////////////////////////////////////////////////////////////////////////////////////////////////////////
/// CONVERSATION FUNCTIONS
//////////////////////////////////////////////////////////////////////////////////////////////////////////////

/// @defgroup KYImgProc_ConversationFunctions Image processing Conversation functions
/// @{

/**
 * @section KYImgProc_ConvertImage KYImgProc_ConvertImage()
 * convert image from source image to destination image
 * @note Use CONVERT_PARAMS_DECL_AND_INIT(convertStruct) for create KYIMGPROC_ConvertParam_Args resources (plain C construction)
 * @subsection C C
 * @code{.c}  KYImgProc_ConvertImage(KYIMGPROC_ConvertParam_Args* convertStruct) @endcode
 * @param [in,out] convertStruct conversation parameters struct
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_ConvertImage(KYIMGPROC_ConvertParam_Args* _convertStruct);

/**
 * @section KYImgProc_GetPackedDataInfo KYImgProc_GetPackedDataInfo()
 * Retrieves information about the packed image data and the corresponding unpacked image details.
 * @subsection C C
 * @code{.c}
 * KYImgProc_GetPackedDataInfo(KYIMGPROC_GetPackedDataInfo_Args* _pArgs)
 * @endcode
 * @param [in] _pArgs Pointer to the structure containing input parameters and a pointer to the output info structure.
 * @return KY_RESULT Status code indicating success or the type of error encountered.
 */

KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_GetPackedDataInfo(KYImgProc_GetPackedDataInfo_Args* _pArgs);

/**
 * @section KYImgProc_PreproccessPackedData KYImgProc_PreproccessPackedData()
 * Performs unpacking of packed image data and fills the destination buffer.
 * @subsection C C
 * @code{.c}
 * KYImgProc_PreproccessPackedData(KYImgProc_PreproccessPackedData_Args* _pArgs)
 * @endcode
 * @param [in] _pArgs Pointer to the structure containing parameters required for unpacking.
 * @return KY_RESULT Status code indicating success or the type of error encountered.
 */

KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_PreproccessPackedData(KYImgProc_PreproccessPackedData_Args* _pArgs);
/// @}





//////////////////////////////////////////////////////////////////////////////////////////////////////////////
/// Save and Record functions
//////////////////////////////////////////////////////////////////////////////////////////////////////////////

/// @defgroup KYImgProc_SaveAndRecord Image processing save and record functions
/// @{
/**
 * @section KYImgProc_SaveImage KYImgProc_SaveImage()
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_SaveImage(KYIMGPROC_SaveImageArgs* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYIMGPROC_SaveImageArgs
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_SaveImage(KYIMGPROC_SaveImageArgs* _pArgs);
/**
 * @section KYImgProc_SaveRawData KYImgProc_SaveRawData()
 * return NOT_IMPLEMENTED if openCV is not compiled 
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_SaveRawData(KYIMGPROC_SaveImageArgs* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYIMGPROC_SaveImageArgs
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_SaveRawData(KYIMGPROC_SaveRawArgs* _pArgs);
/**
 * @section KYImgProc_StartVideoRecord KYImgProc_StartVideoRecord()
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_StartVideoRecord(KYIMGPROC_SaveVideoArgs* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYIMGPROC_SaveVideoArgs
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_StartVideoRecord(KYIMGPROC_SaveVideoArgs* _pArgs);
/**
 * @section KYImgProc_StopVideoRecord KYImgProc_StopVideoRecord()
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_StopVideoRecord(KYImgProc_StopVideoRecordArgs* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYImgProc_StopVideoRecordArgs
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_StopVideoRecord(KYImgProc_StopVideoRecordArgs* _pArgs);
/**
 * @section KYImgProc_WriteVideoFrame KYImgProc_WriteVideoFrame()
 * @subsection C C
 * @code{.c} KY_RESULT KYImgProc_WriteVideoFrame(KYIMGPROC_WriteVideoFrame* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYIMGPROC_WriteVideoFrame
 * @return KY_RESULT Status and error report
 */
KYIMGPROC_API KY_RESULT KYVP_CALLCONV KYImgProc_WriteVideoFrame(KYIMGPROC_WriteVideoFrameArgs* _pArgs);
/// @}




//////////////////////////////////////////////////////////////////////////////////////////////////////////////
/// Comparison functions
//////////////////////////////////////////////////////////////////////////////////////////////////////////////

/// @defgroup KYImgProc_ComparisonFunctions Comparison Functions
/// @{
/**
 * @section KYImgProc_ImageConverterHandleIsInvalid KYImgProc_ImageConverterHandleIsInvalid()
 * @subsection C C
 * @code{.c} KY_BOOL KYImgProc_ImageConverterHandleIsInvalid(KYIMGPROC_IMAGE_CONVERTER_HANDLE _hHandle) @endcode
 * @param [in] h_libHandle @ref KYIMGPROC_IMAGE_CONVERTER_HANDLE
 * @return KY_BOOL Comparison result
 */
KYIMGPROC_API KY_BOOL KYVP_CALLCONV KYImgProc_ImageConverterHandleIsInvalid(KYIMGPROC_IMAGE_CONVERTER_HANDLE _hHandle);
/**
 * @section KYImgProc_ImageConverterHandleIsNull KYImgProc_ImageConverterHandleIsNull()
 * @subsection C C
 * @code{.c} KY_BOOL KYImgProc_ImageConverterHandleIsNull(KYIMGPROC_IMAGE_CONVERTER_HANDLE _hHandle) @endcode
 * @param [in] h_libHandle @ref KYIMGPROC_IMAGE_CONVERTER_HANDLE
 * @return KY_BOOL Comparison result
 */
KYIMGPROC_API KY_BOOL KYVP_CALLCONV KYImgProc_ImageConverterHandleIsNull(KYIMGPROC_IMAGE_CONVERTER_HANDLE _hHandle);
/**
 * @section KYImgProc_ImageConverterHandlesIsEqual KYImgProc_ImageConverterHandlesIsEqual()
 * @subsection C C
 * @code{.c} KY_BOOL KYImgProc_ImageConverterHandlesIsEqual(KYIMGPROC_IMAGE_CONVERTER_HANDLE _hFirstHandle, KYIMGPROC_IMAGE_CONVERTER_HANDLE _hOtherHandle) @endcode
 * @param [in] h_libHandle @ref KYIMGPROC_IMAGE_CONVERTER_HANDLE
 * @param [in] h_libHandle @ref KYIMGPROC_IMAGE_CONVERTER_HANDLE
 * @return KY_BOOL Comparison result
 */
KYIMGPROC_API KY_BOOL KYVP_CALLCONV KYImgProc_ImageConverterHandlesIsEqual(KYIMGPROC_IMAGE_CONVERTER_HANDLE _hFirstHandle, KYIMGPROC_IMAGE_CONVERTER_HANDLE _hOtherHandle);
/**
 * @section KYImgProc_VideoRecorderHandleIsInvalid KYImgProc_VideoRecorderHandleIsInvalid()
 * @subsection C C
 * @code{.c} KY_BOOL KYImgProc_VideoRecorderHandleIsInvalid(KYIMGPROC_VIDEO_RECORDER_HANDLE _hHandle) @endcode
 * @param [in] h_libHandle @ref KYIMGPROC_VIDEO_RECORDER_HANDLE
 * @return KY_BOOL Comparison result
 */
KYIMGPROC_API KY_BOOL KYVP_CALLCONV KYImgProc_VideoRecorderHandleIsInvalid(KYIMGPROC_VIDEO_RECORDER_HANDLE _hHandle);
/**
 * @section KYImgProc_VideoRecorderHandleIsNull KYImgProc_VideoRecorderHandleIsNull()
 * @subsection C C
 * @code{.c} KY_BOOL KYImgProc_VideoRecorderHandleIsNull(KYIMGPROC_VIDEO_RECORDER_HANDLE _hHandle) @endcode
 * @param [in] h_libHandle @ref KYIMGPROC_VIDEO_RECORDER_HANDLE
 * @return KY_BOOL Comparison result
 */
KYIMGPROC_API KY_BOOL KYVP_CALLCONV KYImgProc_VideoRecorderHandleIsNull(KYIMGPROC_VIDEO_RECORDER_HANDLE _hHandle);
/**
 * @section KYImgProc_VideoRecorderHandleIsEqual KYImgProc_VideoRecorderHandleIsEqual()
 * @subsection C C
 * @code{.c} KY_BOOL KYImgProc_VideoRecorderHandleIsEqual(KYIMGPROC_VIDEO_RECORDER_HANDLE _hFirstHandle, KYIMGPROC_VIDEO_RECORDER_HANDLE _hOtherHandle) @endcode
 * @param [in] h_libHandle @ref KYIMGPROC_VIDEO_RECORDER_HANDLE
 * @param [in] h_libHandle @ref KYIMGPROC_VIDEO_RECORDER_HANDLE
 * @return KY_BOOL Comparison result
 */
KYIMGPROC_API KY_BOOL KYVP_CALLCONV KYImgProc_VideoRecorderHandleIsEqual(KYIMGPROC_VIDEO_RECORDER_HANDLE _hFirstHandle, KYIMGPROC_VIDEO_RECORDER_HANDLE _hOtherHandle);
/// @}

