import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'intro',
    'prompt-guide',
    {
      type: 'category',
      label: 'Setup',
      items: [
        'setup/cursor',
        'setup/windsurf',
        'setup/claude-code',
        'setup/openclaw',
      ],
    },
    {
      type: 'category',
      label: 'Concepts',
      items: [
        'concepts/local-estimate-only',
        'concepts/calibration',
        'concepts/cloud-sync',
      ],
    },
  ],
};

export default sidebars;
