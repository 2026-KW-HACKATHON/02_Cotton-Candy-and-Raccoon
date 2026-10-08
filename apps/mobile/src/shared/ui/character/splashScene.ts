// Figma QYCEBzvJCSX22QZ1VmJn8Q / 319:5534, 조회 2026-10-08.
// docs/figma/generate-motion.cjs로 디자인 좌표와 원본 키프레임에서 생성한다.
import type { MotionScene } from "./motionTypes";

export const SPLASH_SCENE: MotionScene = {
  width: 256,
  height: 273.534,
  durationMs: 6000,
  layers: [
    {
      id: "317:3",
      x: 0,
      y: 0,
      width: 256,
      height: 273.534,
      tracks: {
        opacity: [
          {
            at: 0,
            value: 0,
            easing: "ease-out",
          },
          {
            at: 0.075,
            value: 1,
            easing: "linear",
          },
          {
            at: 1,
            value: 1,
            easing: "linear",
          },
        ],
        scaleX: [
          {
            at: 0,
            value: 0.94,
            easing: "ease-out",
          },
          {
            at: 0.09167,
            value: 1,
            easing: "linear",
          },
          {
            at: 1,
            value: 1,
            easing: "linear",
          },
        ],
        scaleY: [
          {
            at: 0,
            value: 0.94,
            easing: "ease-out",
          },
          {
            at: 0.09167,
            value: 1,
            easing: "linear",
          },
          {
            at: 1,
            value: 1,
            easing: "linear",
          },
        ],
      },
      children: [
        {
          id: "img01Shadow",
          x: 57.856,
          y: 243.03496,
          width: 165.5296,
          height: 23.82481,
          asset: "splash-img01Shadow.svg",
        },
        {
          id: "317:7",
          x: 15.1552,
          y: 163.68275,
          width: 77.0816,
          height: 70.46236,
          tracks: {
            rotate: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.10833,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.20833,
                value: 0.175,
                easing: "ease-in-out",
              },
              {
                at: 0.30833,
                value: -0.122,
                easing: "ease-in-out",
              },
              {
                at: 0.4,
                value: 0.087,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            x: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.10833,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.20833,
                value: 1.442,
                easing: "ease-in-out",
              },
              {
                at: 0.30833,
                value: -0.314,
                easing: "ease-in-out",
              },
              {
                at: 0.4,
                value: 0.577,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            y: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.10833,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.20833,
                value: -6.617,
                easing: "ease-in-out",
              },
              {
                at: 0.30833,
                value: 4.733,
                easing: "ease-in-out",
              },
              {
                at: 0.4,
                value: -3.34,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
          },
          children: [
            {
              id: "splash-tail",
              x: -1.9592,
              y: -1.76882,
              width: 81,
              height: 74,
              asset: "splash-tail.svg",
            },
          ],
        },
        {
          id: "img03Body",
          x: 80.82356,
          y: 136.23263,
          width: 125.86648,
          height: 120.95775,
          asset: "splash-img03Body.svg",
        },
        {
          id: "317:23",
          x: 196.736,
          y: 114.85693,
          width: 48.6144,
          height: 52.73736,
          tracks: {
            rotate: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.10833,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.21667000000000003,
                value: -0.044,
                easing: "ease-in-out",
              },
              {
                at: 0.33332999999999996,
                value: 0.035,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
          },
          children: [
            {
              id: "img04ScarfTails",
              x: -1.40009,
              y: -1.40281,
              width: 51.41945,
              height: 55.54298,
              asset: "splash-img04ScarfTails.svg",
            },
          ],
        },
        {
          id: "img05ScarfNeck",
          x: 79.59399,
          y: 125.83111,
          width: 131.47323,
          height: 29.98206,
          asset: "splash-img05ScarfNeck.svg",
        },
        {
          id: "317:29",
          x: 46.72,
          y: 7.02982,
          width: 178.7648,
          height: 134.14107,
          tracks: {
            rotate: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 0.052,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            x: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 3.096,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            y: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 1.083,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
          },
          children: [
            {
              id: "img06Head",
              x: -0.89382,
              y: -1.34141,
              width: 181.23175,
              height: 137.06535,
              asset: "splash-img06Head.svg",
            },
          ],
        },
        {
          id: "317:32",
          x: 67.072,
          y: 22.67597,
          width: 118.2464,
          height: 33.78145,
          tracks: {
            rotate: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 0.052,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            x: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 4.917,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            y: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 0.611,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
          },
          children: [
            {
              id: "img07EarInteriors",
              x: 0,
              y: 0,
              width: 118.2464,
              height: 33.78145,
              asset: "splash-img07EarInteriors.svg",
            },
          ],
        },
        {
          id: "317:36",
          x: 49.8176,
          y: 82.96286,
          width: 170.1632,
          height: 57.38743,
          tracks: {
            rotate: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 0.052,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            x: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 1.132,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            y: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 0.968,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
          },
          children: [
            {
              id: "img09Cheeks",
              x: 0,
              y: 0,
              width: 170.1632,
              height: 57.38743,
              asset: "splash-img09Cheeks.svg",
            },
          ],
        },
        {
          id: "317:41",
          x: 72.5504,
          y: 69.1494,
          width: 126.8992,
          height: 52.05352,
          tracks: {
            rotate: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 0.052,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            x: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 1.993,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            y: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 1.049,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
          },
          children: [
            {
              id: "img08EyeMasks",
              x: 0,
              y: 0,
              width: 126.8992,
              height: 52.05352,
              asset: "splash-img08EyeMasks.svg",
            },
          ],
        },
        {
          id: "317:44",
          x: 101.8624,
          y: 78.0119,
          width: 67.2512,
          height: 29.81521,
          tracks: {
            rotate: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 0.052,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            x: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 2.112,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            y: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.11667,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.3,
                value: 1.025,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
          },
          children: [
            {
              id: "317:45",
              x: 0,
              y: 7.6316,
              width: 15.104,
              height: 17.39676,
              tracks: {
                scaleX: [
                  {
                    at: 0,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 0.22167,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.23833,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.26167,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 1,
                    value: 1,
                    easing: "linear",
                  },
                ],
                scaleY: [
                  {
                    at: 0,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 0.22167,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.23833,
                    value: 0.08,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.26167,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 1,
                    value: 1,
                    easing: "linear",
                  },
                ],
              },
              children: [
                {
                  id: "imgVector2",
                  x: 1.24293,
                  y: 0.97966,
                  width: 12.61814,
                  height: 15.43744,
                  asset: "splash-imgVector2.svg",
                  rotation: -10,
                },
              ],
            },
            {
              id: "317:46",
              x: 52.352,
              y: 0,
              width: 14.8992,
              height: 17.20529,
              tracks: {
                scaleX: [
                  {
                    at: 0,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 0.22167,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.23833,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.26167,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 1,
                    value: 1,
                    easing: "linear",
                  },
                ],
                scaleY: [
                  {
                    at: 0,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 0.22167,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.23833,
                    value: 0.08,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.26167,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 1,
                    value: 1,
                    easing: "linear",
                  },
                ],
              },
              children: [
                {
                  id: "imgVector3",
                  x: 1.13067,
                  y: 0.89166,
                  width: 12.63786,
                  height: 15.42197,
                  asset: "splash-imgVector3.svg",
                  rotation: -9,
                },
              ],
            },
            {
              id: "317:47",
              x: 6.6816,
              y: 10.72253,
              width: 3.84,
              height: 4.18507,
              tracks: {
                scaleX: [
                  {
                    at: 0,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 0.22167,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.23833,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.26167,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 1,
                    value: 1,
                    easing: "linear",
                  },
                ],
                scaleY: [
                  {
                    at: 0,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 0.22167,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.23833,
                    value: 0.08,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.26167,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 1,
                    value: 1,
                    easing: "linear",
                  },
                ],
              },
              children: [
                {
                  id: "imgVector4",
                  x: 0,
                  y: 0,
                  width: 3.84,
                  height: 4.18507,
                  asset: "splash-imgVector4.svg",
                },
              ],
            },
            {
              id: "317:48",
              x: 59.2896,
              y: 2.65328,
              width: 3.84,
              height: 4.21242,
              tracks: {
                scaleX: [
                  {
                    at: 0,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 0.22167,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.23833,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.26167,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 1,
                    value: 1,
                    easing: "linear",
                  },
                ],
                scaleY: [
                  {
                    at: 0,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 0.22167,
                    value: 1,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.23833,
                    value: 0.08,
                    easing: "ease-in-out",
                  },
                  {
                    at: 0.26167,
                    value: 1,
                    easing: "linear",
                  },
                  {
                    at: 1,
                    value: 1,
                    easing: "linear",
                  },
                ],
              },
              children: [
                {
                  id: "imgVector5",
                  x: 0,
                  y: 0,
                  width: 3.84,
                  height: 4.21242,
                  asset: "splash-imgVector5.svg",
                },
              ],
            },
            {
              id: "imgVector6",
              x: 26.88,
              y: 19.55768,
              width: 16.3584,
              height: 10.25753,
              asset: "splash-imgVector6.svg",
            },
            {
              id: "imgVector7",
              x: 29.56783,
              y: 21.86549,
              width: 2.61155,
              height: 1.5389,
              asset: "splash-imgVector7.svg",
            },
          ],
        },
        {
          id: "img11Letter",
          x: 94.60269,
          y: 129.20149,
          width: 92.72742,
          height: 69.89253,
          asset: "splash-img11Letter.svg",
        },
        {
          id: "img12LeftPaw",
          x: 69.38907,
          y: 146.75068,
          width: 42.81748,
          height: 34.04146,
          asset: "splash-img12LeftPaw.svg",
        },
        {
          id: "img13RightPaw",
          x: 162.90109,
          y: 156.57321,
          width: 47.13862,
          height: 38.22599,
          asset: "splash-img13RightPaw.svg",
        },
        {
          id: "317:62",
          x: 5.9648,
          y: 58.91922,
          width: 24.192,
          height: 31.21023,
          tracks: {
            opacity: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.1,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.15833,
                value: 1,
                easing: "ease-in-out",
              },
              {
                at: 0.225,
                value: 0.55,
                easing: "ease-in-out",
              },
              {
                at: 0.29167000000000004,
                value: 1,
                easing: "ease-in-out",
              },
              {
                at: 0.375,
                value: 0.65,
                easing: "ease-in-out",
              },
              {
                at: 0.5,
                value: 1,
                easing: "linear",
              },
              {
                at: 1,
                value: 1,
                easing: "linear",
              },
            ],
            rotate: [
              {
                at: 0,
                value: 0,
                easing: "linear",
              },
              {
                at: 0.1,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.15833,
                value: 0.087,
                easing: "ease-in-out",
              },
              {
                at: 0.225,
                value: 0,
                easing: "ease-in-out",
              },
              {
                at: 0.29167000000000004,
                value: -0.07,
                easing: "ease-in-out",
              },
              {
                at: 0.375,
                value: 0,
                easing: "linear",
              },
              {
                at: 1,
                value: 0,
                easing: "linear",
              },
            ],
            scaleX: [
              {
                at: 0,
                value: 1,
                easing: "ease-in-out",
              },
              {
                at: 0.1,
                value: 0.9,
                easing: "ease-in-out",
              },
              {
                at: 0.15833,
                value: 1.15,
                easing: "ease-in-out",
              },
              {
                at: 0.225,
                value: 1,
                easing: "ease-in-out",
              },
              {
                at: 0.29167000000000004,
                value: 1.12,
                easing: "ease-in-out",
              },
              {
                at: 0.375,
                value: 1,
                easing: "linear",
              },
              {
                at: 1,
                value: 1,
                easing: "linear",
              },
            ],
            scaleY: [
              {
                at: 0,
                value: 1,
                easing: "ease-in-out",
              },
              {
                at: 0.1,
                value: 0.9,
                easing: "ease-in-out",
              },
              {
                at: 0.15833,
                value: 1.15,
                easing: "ease-in-out",
              },
              {
                at: 0.225,
                value: 1,
                easing: "ease-in-out",
              },
              {
                at: 0.29167000000000004,
                value: 1.12,
                easing: "ease-in-out",
              },
              {
                at: 0.375,
                value: 1,
                easing: "linear",
              },
              {
                at: 1,
                value: 1,
                easing: "linear",
              },
            ],
          },
          children: [
            {
              id: "img14AccentMarks",
              x: -2.62967,
              y: -2.63102,
              width: 29.45134,
              height: 36.47227,
              asset: "splash-img14AccentMarks.svg",
            },
          ],
        },
      ],
    },
  ],
  extras: {
    "326:2335": {
      scaleX: [
        {
          at: 0,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.5,
          value: 1.06,
          easing: "ease-in-out",
        },
        {
          at: 1,
          value: 1,
          easing: "linear",
        },
      ],
      scaleY: [
        {
          at: 0,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.5,
          value: 1.06,
          easing: "ease-in-out",
        },
        {
          at: 1,
          value: 1,
          easing: "linear",
        },
      ],
    },
    "321:2205": {
      opacity: [
        {
          at: 0,
          value: 0,
          easing: "linear",
        },
        {
          at: 0.05833,
          value: 0,
          easing: "ease-in-out",
        },
        {
          at: 0.14167,
          value: 1,
          easing: "linear",
        },
        {
          at: 1,
          value: 1,
          easing: "linear",
        },
      ],
      x: [
        {
          at: 0,
          value: 0,
          easing: "linear",
        },
        {
          at: 0.05833,
          value: 0,
          easing: "ease-in-out",
        },
        {
          at: 0.14167,
          value: 0,
          easing: "linear",
        },
        {
          at: 1,
          value: 0,
          easing: "linear",
        },
      ],
      y: [
        {
          at: 0,
          value: 6,
          easing: "linear",
        },
        {
          at: 0.05833,
          value: 6,
          easing: "ease-in-out",
        },
        {
          at: 0.14167,
          value: 0,
          easing: "linear",
        },
        {
          at: 1,
          value: 0,
          easing: "linear",
        },
      ],
    },
    "321:2220": {
      opacity: [
        {
          at: 0,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.01667,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.06667,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.18333,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.23332999999999998,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.35,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.4,
          value: 0.35,
          easing: "linear",
        },
        {
          at: 1,
          value: 0.35,
          easing: "linear",
        },
      ],
    },
    "321:2229": {
      opacity: [
        {
          at: 0,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.05,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.1,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.21667000000000003,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.26667,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.38333,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.43333,
          value: 0.35,
          easing: "linear",
        },
        {
          at: 1,
          value: 0.35,
          easing: "linear",
        },
      ],
    },
    "321:2238": {
      opacity: [
        {
          at: 0,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.08333,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.13333,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.25,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.3,
          value: 0.35,
          easing: "ease-in-out",
        },
        {
          at: 0.41667000000000004,
          value: 1,
          easing: "ease-in-out",
        },
        {
          at: 0.46667000000000003,
          value: 0.35,
          easing: "linear",
        },
        {
          at: 1,
          value: 0.35,
          easing: "linear",
        },
      ],
    },
  },
};

export const SPLASH_ASSETS = {
  "splash-img01Shadow.svg": require("@/assets/figma/motion/splash-img01Shadow.svg"),
  "splash-tail.svg": require("@/assets/figma/motion/splash-tail.svg"),
  "splash-img03Body.svg": require("@/assets/figma/motion/splash-img03Body.svg"),
  "splash-img04ScarfTails.svg": require("@/assets/figma/motion/splash-img04ScarfTails.svg"),
  "splash-img05ScarfNeck.svg": require("@/assets/figma/motion/splash-img05ScarfNeck.svg"),
  "splash-img06Head.svg": require("@/assets/figma/motion/splash-img06Head.svg"),
  "splash-img07EarInteriors.svg": require("@/assets/figma/motion/splash-img07EarInteriors.svg"),
  "splash-img09Cheeks.svg": require("@/assets/figma/motion/splash-img09Cheeks.svg"),
  "splash-img08EyeMasks.svg": require("@/assets/figma/motion/splash-img08EyeMasks.svg"),
  "splash-imgVector2.svg": require("@/assets/figma/motion/splash-imgVector2.svg"),
  "splash-imgVector3.svg": require("@/assets/figma/motion/splash-imgVector3.svg"),
  "splash-imgVector4.svg": require("@/assets/figma/motion/splash-imgVector4.svg"),
  "splash-imgVector5.svg": require("@/assets/figma/motion/splash-imgVector5.svg"),
  "splash-imgVector6.svg": require("@/assets/figma/motion/splash-imgVector6.svg"),
  "splash-imgVector7.svg": require("@/assets/figma/motion/splash-imgVector7.svg"),
  "splash-img11Letter.svg": require("@/assets/figma/motion/splash-img11Letter.svg"),
  "splash-img12LeftPaw.svg": require("@/assets/figma/motion/splash-img12LeftPaw.svg"),
  "splash-img13RightPaw.svg": require("@/assets/figma/motion/splash-img13RightPaw.svg"),
  "splash-img14AccentMarks.svg": require("@/assets/figma/motion/splash-img14AccentMarks.svg"),
};
