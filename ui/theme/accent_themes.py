"""
Accent Theme Presets
Each preset replaces the iris/accent colour scale while keeping the
dark Sand-grey base untouched.  The tuple order matches the iris_3 → iris_12
progression: subtle bg → borders → solid → text.
"""

# Each entry: display label, swatch hex (used in the settings UI),
# and a mapping of Colors class attributes to override.
ACCENT_PRESETS = {
    'iris': {
        'label': 'Iris',
        'swatch': '#5B5BD6',
        'colors': {
            'iris_3':  '#202248', 'iris_4':  '#262A65', 'iris_5':  '#303374',
            'iris_6':  '#3D3E94', 'iris_7':  '#4A4AB8', 'iris_8':  '#5B5BD6',
            'iris_9':  '#5B5BD6', 'iris_10': '#6E6ADE', 'iris_11': '#B1A9FF',
            'iris_12': '#E0DFFE',
        },
    },
    'nebula': {
        'label': 'Nebula',
        'swatch': '#3B9EFF',
        'colors': {
            'iris_3':  '#0D1F35', 'iris_4':  '#0D2D52', 'iris_5':  '#0F3A6E',
            'iris_6':  '#124A8C', 'iris_7':  '#1A5EAC', 'iris_8':  '#2878D4',
            'iris_9':  '#3B9EFF', 'iris_10': '#5AABFF', 'iris_11': '#94CBFF',
            'iris_12': '#D5ECFF',
        },
    },
    'aurora': {
        'label': 'Aurora',
        'swatch': '#00C9A7',
        'colors': {
            'iris_3':  '#0A2520', 'iris_4':  '#0C332C', 'iris_5':  '#0F4038',
            'iris_6':  '#135146', 'iris_7':  '#196657', 'iris_8':  '#00A98C',
            'iris_9':  '#00C9A7', 'iris_10': '#1DD9B8', 'iris_11': '#6EE7D0',
            'iris_12': '#C0F5EC',
        },
    },
    'solar': {
        'label': 'Solar',
        'swatch': '#F59E0B',
        'colors': {
            'iris_3':  '#2D1F05', 'iris_4':  '#3D2B07', 'iris_5':  '#52390A',
            'iris_6':  '#6B4A0D', 'iris_7':  '#8C6212', 'iris_8':  '#B87D18',
            'iris_9':  '#F59E0B', 'iris_10': '#FBAF2A', 'iris_11': '#FCD34D',
            'iris_12': '#FEF0C0',
        },
    },
    'nova': {
        'label': 'Nova',
        'swatch': '#E5484D',
        'colors': {
            'iris_3':  '#2D1010', 'iris_4':  '#3D1616', 'iris_5':  '#521E1E',
            'iris_6':  '#6B2828', 'iris_7':  '#8C3333', 'iris_8':  '#C04040',
            'iris_9':  '#E5484D', 'iris_10': '#F2555A', 'iris_11': '#FF8080',
            'iris_12': '#FFD5D5',
        },
    },
    'forest': {
        'label': 'Forest',
        'swatch': '#30A46C',
        'colors': {
            'iris_3':  '#0E2318', 'iris_4':  '#122E20', 'iris_5':  '#183D2A',
            'iris_6':  '#1E4E36', 'iris_7':  '#256445', 'iris_8':  '#2B8058',
            'iris_9':  '#30A46C', 'iris_10': '#3ABF7E', 'iris_11': '#3DD68C',
            'iris_12': '#C0F5DC',
        },
    },
}
