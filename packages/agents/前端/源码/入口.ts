import { createApp, h } from "vue";
import { createPinia } from "pinia";
import naive, { NConfigProvider, NMessageProvider, darkTheme } from "naive-ui";
import App from "./应用.vue";
import { themeOverrides } from "./主题/深色";
import "./资源/样式.css";

const app = createApp({
  render: () =>
    h(NConfigProvider, { theme: darkTheme, themeOverrides }, () =>
      h(NMessageProvider, null, () => h(App)),
    ),
});

app.use(createPinia()).use(naive).mount("#app");
