#pragma once

#include <string>
#include <map>

struct EnumParameterDefinition
{
    struct Entry
    {
    public:
        #ifndef _MSC_VER 
        // otherwise (g++): error: no matching function for call to "EnumParameterDefinition::Entry::Entry(<brace-enclosed initializer list>)"
        // see https://stackoverflow.com/questions/18184096/c11-struct-initialization-compilation-error: This restriction has been lifted in C++14
        Entry() {};
        Entry(int64_t  _value, const char* _name, const char* _displayName)
            :value(_value), name(_name), displayName(_displayName)
        {
        }
        #endif

        int64_t     value = 0;
        std::string name;
        std::string displayName;

    };
    std::map<int64_t, Entry> m_Entries;
};

KAYA_API FGSTATUS  KY_GetEnumParameterDefinition(KYHANDLE handle, const char* paramName, EnumParameterDefinition& enumParameterDefinition);




