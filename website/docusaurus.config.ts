import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";
import { themes as prismThemes } from "prism-react-renderer";

// The public site belongs to the canonical GitHub repository. Environment
// overrides keep preview and fork builds possible without changing source.
const organizationName = process.env.DOCS_ORG ?? "romarayt";
const projectName = process.env.DOCS_REPO ?? "raytsystem-public-os";
const siteUrl = process.env.DOCS_URL ?? `https://${organizationName}.github.io`;
const baseUrl = process.env.DOCS_BASE_URL ?? `/${projectName}/`;

// Repository URL used for "Edit this page" links. The website lives under
// website/ inside the raytsystem repository.
const editUrl = `https://github.com/${organizationName}/${projectName}/tree/main/website/`;

const config: Config = {
  title: "Система Райта — документация",
  tagline: "Локальная агентная система: установка, интерфейс, граф, задачи и безопасность",
  favicon: "img/favicon.svg",

  url: siteUrl,
  baseUrl,
  organizationName,
  projectName,
  trailingSlash: false,

  // Broken links must fail the build so documentation cannot rot silently.
  onBrokenLinks: "throw",
  onBrokenAnchors: "throw",

  i18n: {
    // Russian is the shipped locale. The tree is English-ready: adding "en"
    // to `locales` and running `docusaurus write-translations` is enough to
    // start an English version without restructuring content.
    defaultLocale: "ru",
    locales: ["ru"],
    localeConfigs: {
      ru: { label: "Русский", direction: "ltr", htmlLang: "ru-RU" },
    },
  },

  markdown: {
    mermaid: false,
    hooks: {
      // A relative Markdown link that does not resolve is a build failure, so
      // stale cross-references cannot merge unnoticed.
      onBrokenMarkdownLinks: "throw",
    },
  },

  presets: [
    [
      "classic",
      {
        docs: {
          routeBasePath: "/",
          sidebarPath: "./sidebars.ts",
          editUrl,
          // Surfaces the last meaningful content change as verifiable metadata
          // (git author/date), not a hand-typed "current" date.
          showLastUpdateTime: true,
          showLastUpdateAuthor: true,
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
        sitemap: {
          changefreq: "weekly",
          priority: 0.5,
        },
      } satisfies Preset.Options,
    ],
  ],

  themes: [
    [
      "@easyops-cn/docusaurus-search-local",
      {
        // Fully local, offline full-text search. No paid service, no network
        // request at runtime.
        hashed: true,
        language: ["en", "ru"],
        indexDocs: true,
        indexBlog: false,
        docsRouteBasePath: "/",
        highlightSearchTermsOnTargetPage: true,
        searchResultLimits: 12,
        searchBarShortcut: true,
      },
    ],
  ],

  themeConfig: {
    image: "img/logo.svg",
    colorMode: {
      defaultMode: "dark",
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "Система Райта",
      logo: {
        alt: "Система Райта",
        src: "img/logo.svg",
      },
      items: [
        {
          type: "docSidebar",
          sidebarId: "knowledgeBase",
          position: "left",
          label: "База знаний",
        },
        {
          to: "/coverage",
          label: "Покрытие",
          position: "left",
        },
        {
          href: `https://github.com/${organizationName}/${projectName}`,
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "База знаний",
          items: [
            { label: "Начало работы", to: "/getting-started/what-is-raytsystem" },
            { label: "Интерфейс", to: "/interface/overview" },
            { label: "Граф проекта", to: "/code-graph/overview" },
            { label: "Решение проблем", to: "/troubleshooting/overview" },
          ],
        },
        {
          title: "Проект",
          items: [
            { label: "Безопасность", to: "/security/overview" },
            { label: "Участие в разработке", to: "/development/contributing" },
            { label: "Покрытие документации", to: "/coverage" },
          ],
        },
      ],
      copyright:
        "raytsystem — Apache-2.0. Документация является частью продукта и синхронизируется с кодом.",
    },
    prism: {
      theme: prismThemes.oneLight,
      darkTheme: prismThemes.oneDark,
      additionalLanguages: ["bash", "toml", "yaml", "json", "python"],
    },
    tableOfContents: {
      minHeadingLevel: 2,
      maxHeadingLevel: 4,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
