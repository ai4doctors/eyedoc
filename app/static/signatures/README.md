# Provider Signatures

Place signature images in this directory to have them automatically included in generated PDFs.

## Naming Convention

The filename should match the provider name, converted to a "slug":

1. Remove titles (Dr., MD, OD, MBA, PhD, DO, NP, PA)
2. Convert to lowercase
3. Replace non-alphanumeric characters with underscores
4. Remove duplicate underscores

### Examples

| Provider Name | Filename |
|---------------|----------|
| Dr. Henry Reis | `henry_reis.png` |
| Dr. Jane Smith, MD | `jane_smith.png` |
| Maria Garcia OD | `maria_garcia.png` |
| John O'Brien MD | `john_o_brien.png` |

## Image Requirements

- Format: PNG (preferred) or JPG/JPEG
- Recommended size: 200-400px wide
- Background: Transparent (for PNG) or white
- The signature image should include the provider's printed name if desired

## How It Works

When generating a PDF letter, the system:
1. Looks for a signature image matching the "From" provider name
2. If found, adds the image after "Kind regards," 
3. The provider's typed name is NOT added (since the signature image contains it)
4. If no signature image is found, the provider's name is typed below "Kind regards,"

## Environment Variable

You can override the signatures directory location:

```
SIGNATURE_DIR=path/to/signatures
```

Default: `static/signatures`
