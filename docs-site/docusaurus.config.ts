import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const DISCORD_INVITE_URL = 'https://discord.gg/AX9t8jNR2J';

const config: Config = {
  title: 'l6e-mcp',
  tagline: 'Session-scoped budget enforcement for AI coding assistants.',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  url: 'https://mcp.l6e.ai',
  baseUrl: '/',

  organizationName: 'l6e-ai',
  projectName: 'l6e-mcp',

  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'warn',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          routeBasePath: '/',
          editUrl: 'https://github.com/l6e-ai/l6e-mcp/tree/main/docs-site/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  plugins: [],

  themeConfig: {
    image: 'img/l6e-mcp-social-card.jpg',
    colorMode: {
      defaultMode: 'light',
      respectPrefersColorScheme: true,
      disableSwitch: false,
    },
    navbar: {
      title: 'l6e-mcp',
      logo: {
        alt: 'l6e logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docs',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://pypi.org/project/l6e-mcp/',
          label: 'PyPI',
          position: 'right',
        },
        {
          href: DISCORD_INVITE_URL,
          label: 'Discord',
          position: 'right',
        },
        {
          href: 'https://github.com/l6e-ai/l6e-mcp',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'light',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Introduction', to: '/'},
            {label: 'Setup: Cursor', to: '/setup/cursor'},
            {label: 'Setup: Claude Code', to: '/setup/claude-code'},
            {label: 'Local Enforcement', to: '/concepts/local-estimate-only'},
          ],
        },
        {
          title: 'Community',
          items: [
            {label: 'Discord', href: DISCORD_INVITE_URL},
            {label: 'GitHub Discussions', href: 'https://github.com/l6e-ai/l6e-mcp/discussions'},
            {label: 'Issues', href: 'https://github.com/l6e-ai/l6e-mcp/issues'},
          ],
        },
        {
          title: 'More',
          items: [
            {label: 'GitHub', href: 'https://github.com/l6e-ai/l6e-mcp'},
            {label: 'PyPI', href: 'https://pypi.org/project/l6e-mcp/'},
            {label: 'l6e.ai', href: 'https://l6e.ai'},
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} l6e AI. Apache 2.0 Licensed.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
