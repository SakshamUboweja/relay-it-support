import type { Service, User } from './domain';
export const users: User[] = [
  {
    id: 'maya',
    name: 'Maya Chen',
    role: 'employee',
    location: 'San Francisco',
    device: 'MacBook Pro · macOS',
    scope: 'sf',
    external_account: null,
  },
  {
    id: 'jordan',
    name: 'Jordan Ellis',
    role: 'employee',
    location: 'London',
    device: 'ThinkPad · Windows 11',
    scope: 'london',
    external_account: null,
  },
  {
    id: 'alex',
    name: 'Alex Morgan',
    role: 'operator',
    location: 'San Francisco',
    device: 'MacBook Pro · macOS',
    scope: 'operators',
    external_account: null,
  },
];
export const catalog: {
  id: Service;
  name: string;
  aliases: string[];
  team: string;
  critical: boolean;
}[] = [
  {
    id: 'vpn',
    name: 'Corporate VPN',
    aliases: ['vpn', 'globalprotect', 'tunnel'],
    team: 'Network',
    critical: false,
  },
  {
    id: 'sso',
    name: 'Single sign-on',
    aliases: [
      'sso',
      'okta',
      'single sign',
      'sign-in',
      'login',
      'log in',
      'mfa',
      'password',
    ],
    team: 'Identity & Access',
    critical: true,
  },
  {
    id: 'wifi',
    name: 'Office Wi-Fi',
    aliases: ['wifi', 'wi-fi', 'wireless'],
    team: 'Network',
    critical: false,
  },
  {
    id: 'laptop',
    name: 'Managed laptops',
    aliases: [
      'laptop',
      'display',
      'screen',
      'monitor',
      'battery',
      'trackpad',
      'keyboard',
      'macbook',
      'thinkpad',
      'computer',
    ],
    team: 'Endpoint',
    critical: false,
  },
  {
    id: 'atlas',
    name: 'Atlas',
    aliases: ['atlas', 'internal application', 'dashboard'],
    team: 'Business Applications',
    critical: true,
  },
];
export const articles = [
  [
    'wifi',
    'Reconnect to Northstar Wi-Fi',
    'Turn Wi-Fi off, wait 10 seconds, then reconnect to Northstar-Secure. Do not forget a network or share credentials.',
    'wifi',
  ],
  [
    'laptop',
    'Check your external display connection',
    'Save your work. Reconnect the display cable at both ends, then select the correct monitor input. Stop if a cable or device feels hot.',
    'display',
  ],
  [
    'vpn',
    'VPN authentication after a password change',
    'A recent password change can leave cached VPN credentials. This is a hypothesis; Identity & Access checks authentication without asking for passwords.',
    '',
  ],
  [
    'sso',
    'SSO sign-in diagnostic information',
    'Record the exact sign-in error and time. Never include passwords, backup codes, or session tokens.',
    '',
  ],
  [
    'atlas',
    'Atlas service health',
    'Check the approved Atlas advisory for your location before trying local fixes.',
    '',
  ],
  [
    'wifi',
    'Wi-Fi network reachability',
    'Record the office location and whether the managed device can see Northstar-Secure. Leave other users affected unknown.',
    '',
  ],
  [
    'vpn',
    'VPN reachability versus authentication',
    'A timeout before sign-in is a network candidate. Rejected credentials after a password change follow Identity & Access policy.',
    '',
  ],
  [
    'laptop',
    'Battery swelling requires human review',
    'Stop using a device with visible swelling or unusual heat. Do not open the device. Contact support.',
    '',
  ],
  [
    'sso',
    'Unexpected MFA prompt',
    'An unsolicited authentication approval is suspicious. Stop routine troubleshooting and use restricted security review.',
    '',
  ],
  [
    'atlas',
    'Atlas blank dashboard',
    'Note the dashboard name and time of failure. Do not paste customer information into the report.',
    '',
  ],
  [
    'wifi',
    'Guest network limitations',
    'The guest network cannot reach internal services. Do not change managed security settings.',
    '',
  ],
  [
    'vpn',
    'VPN intermittent connectivity',
    'Record whether the connection drops after authentication. A dropped tunnel does not establish a credential problem.',
    '',
  ],
  [
    'laptop',
    'External keyboard connection',
    'Save work, then check the external keyboard connection. Do not install unapproved drivers.',
    '',
  ],
  [
    'sso',
    'Password reset safety',
    'Use your organization’s approved identity recovery page. Support will not ask for passwords or recovery codes.',
    '',
  ],
  [
    'atlas',
    'Atlas error messages',
    'Record an error code without copying confidential records. An error alone does not prove a service outage.',
    '',
  ],
  [
    'wifi',
    'Office-specific incidents',
    'A wireless incident in one office is not automatically relevant to another office.',
    '',
  ],
  [
    'vpn',
    'Report a VPN issue',
    'Include the error text, time, and whether sign-in completed if known. Missing optional diagnostics should not block support.',
    '',
  ],
  [
    'laptop',
    'Managed software failures',
    'Report application crashes with the app name and symptom. Do not run scripts from an untrusted source.',
    '',
  ],
  [
    'sso',
    'Recognize phishing questions',
    'An informational question about reporting phishing is not itself an active compromise. Use general support if unclear.',
    '',
  ],
  [
    'atlas',
    'Atlas access requests',
    'New access requires a separate approval workflow and goes through general intake in this demo.',
    '',
  ],
] as const;
