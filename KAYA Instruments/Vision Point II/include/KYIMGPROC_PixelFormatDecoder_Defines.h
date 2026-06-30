#ifndef KYIMGPROC_PIXELFORMAT_DECODER_H_
#define KYIMGPROC_PIXELFORMAT_DECODER_H_

// Data width of the pixel , defined in section 9.4.1.2 of the JIIA CXP standard document
typedef enum _kyvp_video_data_width
{
    KYVP_DATA_WIDTH_UNKNOWN          = 0x00, // ?bit per pixel data
    KYVP_DATA_WIDTH_8BIT             = 0x01, // 8bit per pixel data
    KYVP_DATA_WIDTH_10BIT            = 0x02, // 10bit per pixel data
    KYVP_DATA_WIDTH_12BIT            = 0x03, // 12bit per pixel data
    KYVP_DATA_WIDTH_14BIT            = 0x04, // 14bit per pixel data
    KYVP_DATA_WIDTH_16BIT            = 0x05  // 16bit per pixel data
}KYVP_VIDEO_DATA_WIDTH;

// Data sub-types of the pixel , defined in section 9.4.1 of the JIIA CXP standard document
typedef enum _kyvp_video_data_subtype
{
    KYVP_DATA_SUBTYPE_NONE           = 0x00,
    // Planar subtypes
    KYVP_DATA_SUBTYPE_PLANAR_RY      = 0x01, // Standard usage: R(RGB) / Y(YUV) / Y(YCbCr)
    KYVP_DATA_SUBTYPE_PLANAR_GUCB    = 0x02, // Standard usage: G(RGB) / U(YUV) / Cb(YCbCr)
    KYVP_DATA_SUBTYPE_PLANAR_BVCR    = 0x03, // Standard usage: B(RGB) / V(YUV) / Cr(YCbCr)
    KYVP_DATA_SUBTYPE_PLANAR_RGB     = 0x04, // Standard usage: RGB planar

    // Bayer subtypes
    KYVP_DATA_SUBTYPE_BAYER_GR       = 0x01, // 1st line transmission order G, R. 2nd line transmission order B, G
    KYVP_DATA_SUBTYPE_BAYER_RG       = 0x02, // 1st line transmission order R, G. 2nd line transmission order G, B
    KYVP_DATA_SUBTYPE_BAYER_GB       = 0x03, // 1st line transmission order G, B. 2nd line transmission order R, G
    KYVP_DATA_SUBTYPE_BAYER_BG       = 0x04, // 1st line transmission order B, G. 2nd line transmission order G, R
    // YUV subtypes
    KYVP_DATA_SUBTYPE_YUV_411        = 0x01, // Transmission order Y, Y, U, Y, Y, V
    KYVP_DATA_SUBTYPE_YUV_422        = 0x02, // Transmission order Y, U, Y, V
    KYVP_DATA_SUBTYPE_YUV_444        = 0x03, // Transmission order Y, U, V
    // YCbCr subtypes
    KYVP_DATA_SUBTYPE_YCBCR_411      = 0x01, // Transmission order Y, Y, Cb, Y, Y, Cr
    KYVP_DATA_SUBTYPE_YCBCR_422      = 0x02, // Transmission order Y, Cb, Y, Cr
    KYVP_DATA_SUBTYPE_YCBCR_444      = 0x03, // Transmission order Y, Cb, Cr
    KYVP_DATA_SUBTYPE_INVALID        = 0x0F  // Invalid data subtype
}KYVP_VIDEO_DATA_SUBTYPE;

// Data types of the pixel, defined in section 9.4.1 of the JIIA CXP standard document
typedef enum _kyvp_video_data_type
{
    KYVP_DATA_TYPE_RAW               = 0x00, // This is used for raw data type.
    KYVP_DATA_TYPE_MONO              = 0x01, // This is used for luminance data. This has no sub-types. This is defined in Table 27 of JIIA CXP standard document.
    KYVP_DATA_TYPE_PLANAR            = 0x02, // This is used for planar data, such as individual red, green or blue planes, additional alpha (overlay) planes, or the separate planes in YUV420. This is defined in Table 28 JIIA CXP standard document. Subtipes uncludes all the DATA_SUBTYPE_PLANAR_xx
    KYVP_DATA_TYPE_BAYER             = 0x03, // This is used for Bayer data. This is defined in Table 29 JIIA CXP standard document. Subtipes include all DATA_SUBTYPE_BAYER_xx
    KYVP_DATA_TYPE_RGB               = 0x04, // This is used for RGB data, transmitted in the order red, green, blue. This has no sub-types. This is defined in Table 30 JIIA CXP standard document.
    KYVP_DATA_TYPE_RGBA              = 0x05, // This is used for RGBA data, where 'A' is the alpha (or overlay) plane, transmitted in the order red, green, blue, alpha. This has no sub-types. This is defined in Table 31 JIIA CXP standard document.
    KYVP_DATA_TYPE_YUV               = 0x06, // This is used for YUV data.This is defined in Table 32 JIIA CXP standard document.Subtipes include all DATA_SUBTYPE_YUV_xxx
    KYVP_DATA_TYPE_YCBCR601          = 0x07, // This is used for YCbCr data, as specified by ITU-R BT.601.This is defined in Table 33 JIIA CXP standard document. Subtipes include all DATA_SUBTYPE_UCBCR_xxx
    KYVP_DATA_TYPE_YCBCR709          = 0x08, // This is used for YCbCr data, as specified by ITU-R BT.709. This is defined in Table 34 JIIA CXP standard document. Subtipes include all DATA_SUBTYPE_UCBCR_xxx
    KYVP_DATA_TYPE_INVALID           = 0xFF  // Invalid data type
}KYVP_VIDEO_DATA_TYPE;

//The pixel format code is formed as shown in Table 24 of JIIA CXP document. Note that the value 0x0000 is reserved for 'raw' data that does not match any defined format, such as user-specific formats.
#pragma pack(push, 1)
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYVP_VIDEO_PIXELIF KYVP_VIDEO_PIXELIF
 * The pixel format code is formed as shown in Table 24 of the JIIA CXP document. Note that the value 0x0000 is reserved for 'RAW' data that does not match any defined format, such as user-specific formats
 * @code{.c}
    typedef struct _kyvp_video_pixelif
    {
    KYVP_VIDEO_DATA_WIDTH data_width : 4; // Data Width
    KYVP_VIDEO_DATA_SUBTYPE data_subtype : 4; // Sub-type
    VIDEO_DATA_TYPE data_type : 8; // Data Type
    }KYVP_VIDEO_PIXELIF;
 * @endcode
 * | Structure Field    | Type                      | Description                       |
 * | -----------        | -------------             | ---------                         |
 * | data_width         | KYVP_VIDEO_DATA_WIDTH     | Data Width (4 bit value)          |
 * | data_subtype       | KYVP_VIDEO_DATA_SUBTYPE   | Sub-type (4 bit value)            |
 * | data_type          | KYVP_VIDEO_DATA_TYPE      | Data Type (8 bit value)           |
*/
#pragma warning(disable : 4214)
typedef struct _KYVP_VIDEO_PIXELIF
{
    KYVP_VIDEO_DATA_WIDTH       data_width : 4;    // Data Width
    KYVP_VIDEO_DATA_SUBTYPE     data_subtype : 4;    // Sub-type
    KYVP_VIDEO_DATA_TYPE        data_type : 8;    // Data Type
}KYVP_VIDEO_PIXELIF;
#pragma warning(default : 4214)
#pragma pack(pop)

#ifdef __cplusplus
static const int KYIMGPROC_KYVP_PIXEL_FORMAT_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_KYVP_PIXEL_FORMAT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYVP_PIXEL_FORMAT KYVP_PIXEL_FORMAT
 * Pixel format description
 * | Structure Field    | Type                      | Description                               | Remarks                                           |
 * | -------------      | -----                     | -----------                               | ----                                              |
 * | version            | uint32_t                  | Struct version                            | Must be between 1 and PIXEL_FORMAT_MAX_VERSION    |
 * | pszPFNCName        | const char*               | PFNC pixel format name                    |                                                   |
 * | uPFNCValue         | uint32_t                  | PFNC pixel format 32-bit value            | https://www.emva.org/wp-content/uploads/GenICamPixelFormatValues.pdf |
 * | szCxpName          | const char*               | Pixel format CXP name                     |                                                   |
 * | uCxpValue          | uint32_t                  | CXP pixel format 32-bit  value            |                                                   |
 * | bIsPacked          | KY_BOOL                   | Packed or unpacked data for 10,12,14 bit  |                                                   |
 * | bRGBReordered      | KY_BOOL                   | It's BGR if true, else RGB                |                                                   |
 * | uChannelsPerPixel  | uint8_t                   | Number of channels per pixel              |                                                   |
 * | KYVP_VIDEO_PIXELIF | KYVP_VIDEO_PIXELIF        | VIDEO_PIXELIF structure                   |                                                   |
 */
#pragma pack(push, 1)
typedef struct _KYVP_PIXEL_FORMAT
{
    uint32_t            version             KYVP_MEMBER_DEFAULT(KYIMGPROC_KYVP_PIXEL_FORMAT_STRUCT_VERSION);
    const char*         pszPFNCName         KYVP_MEMBER_DEFAULT(nullptr);
    uint32_t            uPFNCValue          KYVP_MEMBER_DEFAULT(0);
    const char*         pszCxpName          KYVP_MEMBER_DEFAULT(nullptr);
    uint32_t            uCxpValue           KYVP_MEMBER_DEFAULT(0);
    KY_BOOL             bIsPacked           KYVP_MEMBER_DEFAULT(KY_FALSE);
    KY_BOOL             bRGBReordered       KYVP_MEMBER_DEFAULT(KY_FALSE);
    uint8_t             uChannelsPerPixel   KYVP_MEMBER_DEFAULT(0);
    KYVP_VIDEO_PIXELIF  pVPPixelIF          KYVP_MEMBER_DEFAULT({});

}KYVP_PIXEL_FORMAT;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYIMGPROC_PIXEL_DECODER_STRUCT_VERSION = 1;
#else
#define KYIMGPROC_PIXEL_DECODER_STRUCT_VERSION 1
#endif
/**
 * @page KYVPImageProcessingStructs Image Processing Library Structs
 * @section KYIMGPROC_PIXEL_DECODER KYIMGPROC_PIXEL_DECODER
 * Pixel format decoder struct
 * | Structure Field        | Type                      | Description                                   | Remarks                                               |
 * | -------------          | -----                     | -----------                                   | ----                                                  |
 * | version                | uint32_t                  | Struct version                                |                                                       |
 * | _uValue                | uint32_t                  | PFNC 32-bit value or CXP 16-bit value         |                                                       |
 * | _pszPixelFormatName    | const char*               | PFNC Name value of pixel format               | PFNC values read documentation: https://www.emva.org/wp-content/uploads/GenICamPixelFormatValues.pdf |
 * | _pVP_PIXEL_FORMAT      | KYVP_PIXEL_FORMAT         | Pointer to KYVP_PIXEL_FORMAT struct           |                                                       |
 */
#pragma pack(push, 1)
typedef struct _KYIMGPROC_PIXEL_DECODER
{
    uint32_t            version                 KYVP_MEMBER_DEFAULT(KYIMGPROC_PIXEL_DECODER_STRUCT_VERSION);
    uint32_t            uValue                 KYVP_MEMBER_DEFAULT(0);
    const char*         pszPixelFormatName     KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_PIXEL_FORMAT*  pVP_PIXEL_FORMAT       KYVP_MEMBER_DEFAULT(nullptr);

}KYIMGPROC_PIXEL_DECODER;
#pragma pack(pop)

#endif // #ifndef KYIMGPROC_PIXELFORMAT_DECODER_H_